# netplay.py
import json
import struct
import socket
import threading
import time
import zlib

from PyQt6.QtCore import QObject, pyqtSignal


# GBA Memory-Mapped SIO / I/O Register Offsets inside io[0x400] (0x04000000 base)
REG_DISPCNT = 0x000
REG_SIODATA32 = 0x120
REG_SIOMULTI0 = 0x120
REG_SIOMULTI1 = 0x122
REG_SIOMULTI2 = 0x124
REG_SIOMULTI3 = 0x126
REG_SIOCNT = 0x128
REG_SIODATA8 = 0x12A
REG_SIOMLT_SEND = 0x12A
REG_KEYINPUT = 0x130
REG_RCNT = 0x134
REG_JOYCNT = 0x140


class SIOStateProbe:
    """
    Locates and inspects mGBA's 1 KB I/O register block inside GBASerializedState
    by anchoring against live PRAM (0x05000000).
    """

    def __init__(self):
        self.io_offset = None

    def reset(self):
        self.io_offset = None

    def _find_io_offset(self, state_bytes: bytes, pram_bytes: bytes | None) -> int | None:
        if not state_bytes or len(state_bytes) < 2048:
            return None

        if pram_bytes and len(pram_bytes) >= 1024 and any(pram_bytes[:1024]):
            probe = pram_bytes[:256]
            start = 1024
            while True:
                idx = state_bytes.find(probe, start)
                if idx == -1:
                    break
                if idx >= 1024 and state_bytes[idx:idx + 1024] == pram_bytes[:1024]:
                    cand_io = idx - 1024
                    key_val = struct.unpack_from("<H", state_bytes, cand_io + REG_KEYINPUT)[0]
                    if (key_val & 0xFC00) == 0:
                        self.io_offset = cand_io
                        return cand_io
                start = idx + 1
        return None

    def read_sio_snapshot(self, state_bytes: bytes, pram_bytes: bytes | None = None) -> dict | None:
        if not state_bytes:
            return None

        io_off = self.io_offset
        if io_off is None or io_off + 0x200 > len(state_bytes):
            io_off = self._find_io_offset(state_bytes, pram_bytes)
        elif pram_bytes and len(pram_bytes) >= 1024 and any(pram_bytes[:1024]):
            # Re-verify cached offset in case state version/layout shifted
            if state_bytes[io_off + 1024:io_off + 1280] != pram_bytes[:256]:
                io_off = self._find_io_offset(state_bytes, pram_bytes)

        if io_off is None:
            return None

        siocnt = struct.unpack_from("<H", state_bytes, io_off + REG_SIOCNT)[0]
        siodata32 = struct.unpack_from("<I", state_bytes, io_off + REG_SIODATA32)[0]
        siomlt_send = struct.unpack_from("<H", state_bytes, io_off + REG_SIOMLT_SEND)[0]
        rcnt = struct.unpack_from("<H", state_bytes, io_off + REG_RCNT)[0]

        return {
            "io_offset": io_off,
            "siocnt": siocnt,
            "siodata32": siodata32,
            "siomlt_send": siomlt_send,
            "rcnt": rcnt,
            "mode_label": self.decode_sio_mode(siocnt, rcnt),
        }

    def patch_peer_sio_into_state(
        self,
        state_bytes: bytes,
        peer_sio: dict,
        is_host: bool,
        pram_bytes: bytes | None = None,
    ) -> bytes | None:
        """Injects remote peer's SIO data register into local GBASerializedState I/O block."""
        snap = self.read_sio_snapshot(state_bytes, pram_bytes)
        if not snap or not peer_sio:
            return None

        io_off = snap["io_offset"]
        local_siocnt = snap["siocnt"]
        local_rcnt = snap["rcnt"]

        # Only bridge when serial port is configured for Normal or Multiplayer mode
        rcnt_hi = (local_rcnt >> 14) & 0x3
        if rcnt_hi == 0b10:  # General-purpose GPIO
            return None

        buf = bytearray(state_bytes)
        sio_mode = (local_siocnt >> 12) & 0x3

        if sio_mode == 0b10:  # Multiplayer 16-bit mode
            peer_word = int(peer_sio.get("siomlt_send", 0xFFFF)) & 0xFFFF
            target_reg = REG_SIOMULTI1 if is_host else REG_SIOMULTI0
            struct.pack_into("<H", buf, io_off + target_reg, peer_word)
            # Clear Start/Busy bit (bit 7) if peer also responded so ROM sees transfer completion
            if (local_siocnt & 0x0080) and (int(peer_sio.get("siocnt", 0)) & 0x0080):
                struct.pack_into("<H", buf, io_off + REG_SIOCNT, local_siocnt & ~0x0080)
            return bytes(buf)

        if sio_mode in (0b00, 0b01):  # Normal 8-bit / 32-bit SPI mode
            if local_siocnt & 0x0080:
                peer_d32 = int(peer_sio.get("siodata32", 0xFFFFFFFF)) & 0xFFFFFFFF
                struct.pack_into("<I", buf, io_off + REG_SIODATA32, peer_d32)
                struct.pack_into("<H", buf, io_off + REG_SIOCNT, local_siocnt & ~0x0080)
                return bytes(buf)

        return None

    @staticmethod
    def decode_sio_mode(siocnt: int, rcnt: int) -> str:
        rcnt_mode = (rcnt >> 14) & 0x3
        if rcnt_mode == 0b10:
            return "GPIO / General"
        if rcnt_mode == 0b11:
            return "JOY Bus"
        sio_mode = (siocnt >> 12) & 0x3
        if sio_mode == 0b00:
            return "Normal 8-bit"
        if sio_mode == 0b01:
            return "Normal 32-bit"
        if sio_mode == 0b10:
            return "Multiplayer 16-bit"
        return "UART"


class NetplayEngine(QObject):
    """
    TCP Socket Netplay & Link-Cable Bridge for Process-Isolated GBA Instances.
    Supports:
      - 'lockstep': Frame-accurate input sync with Host->Client initial state sync.
      - 'link_sio': Dual-instance SIO Link-Cable register frame bridge.
    """

    status_changed = pyqtSignal(str, str)       # state_code ("OFFLINE"|"HOSTING"|"CONNECTING"|"CONNECTED"), detail_msg
    ping_updated = pyqtSignal(float)            # RTT in milliseconds
    peer_identified = pyqtSignal(str, str)      # peer_name, peer_game_code
    state_sync_received = pyqtSignal(bytes)     # raw serialized state from host
    error_occurred = pyqtSignal(str)

    MAX_PACKET_SIZE = 16 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        self._active = False
        self._connected = False
        self._is_host = False
        self._mode = "lockstep"
        self._frame_delay = 1
        self._coop_role = "shared"  # "shared" | "swap_p2" | "host_only"
        self._player_name = "Player"
        self._local_game_code = "UNKNOWN"
        self._local_title = "Unknown"

        self._listen_sock = None
        self._peer_sock = None
        self._net_thread = None

        self._remote_inputs = {}    # frame_num -> int mask
        self._remote_sio = {}       # frame_num -> dict
        self._pending_state_bytes = None
        self._ping_ms = 0.0
        self._last_ping_sent = 0.0
        self._synced_frames = 0

        self.sio_probe = SIOStateProbe()
        self.last_local_sio = None
        self.last_remote_sio = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_host(self) -> bool:
        return self._is_host

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def frame_delay(self) -> int:
        return self._frame_delay

    @property
    def coop_role(self) -> str:
        return self._coop_role

    @coop_role.setter
    def coop_role(self, val: str):
        self._coop_role = val

    @property
    def ping_ms(self) -> float:
        return self._ping_ms

    @property
    def synced_frames(self) -> int:
        return self._synced_frames

    def set_local_rom_info(self, title: str, game_code: str):
        self._local_title = title or "Unknown"
        self._local_game_code = game_code or "UNKNOWN"

    # =========================================================================
    # SESSION LIFECYCLE (HOST / JOIN / STOP)
    # =========================================================================
    def start_host(
        self,
        port: int = 5738,
        mode: str = "lockstep",
        frame_delay: int = 1,
        player_name: str = "Host",
        coop_role: str = "shared",
        initial_state_bytes: bytes | None = None,
    ) -> bool:
        self.stop()
        with self._lock:
            self._active = True
            self._connected = False
            self._is_host = True
            self._mode = mode
            self._frame_delay = max(0, min(6, int(frame_delay)))
            self._player_name = player_name or "Host"
            self._coop_role = coop_role
            self._pending_state_bytes = initial_state_bytes
            self._remote_inputs.clear()
            self._remote_sio.clear()
            self._synced_frames = 0
            self.sio_probe.reset()

        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", int(port)))
            srv.listen(1)
            srv.settimeout(0.5)
            self._listen_sock = srv
        except OSError as e:
            self._active = False
            self.error_occurred.emit(f"Failed to bind port {port}: {e}")
            self.status_changed.emit("OFFLINE", f"Bind error: {e}")
            return False

        self.status_changed.emit("HOSTING", f"Listening on port {port} ({mode})...")
        self._net_thread = threading.Thread(target=self._host_loop, daemon=True)
        self._net_thread.start()
        return True

    def connect_to_host(
        self,
        host: str = "127.0.0.1",
        port: int = 5738,
        mode: str = "lockstep",
        frame_delay: int = 1,
        player_name: str = "Peer",
    ) -> bool:
        self.stop()
        with self._lock:
            self._active = True
            self._connected = False
            self._is_host = False
            self._mode = mode
            self._frame_delay = max(0, min(6, int(frame_delay)))
            self._player_name = player_name or "Peer"
            self._remote_inputs.clear()
            self._remote_sio.clear()
            self._synced_frames = 0
            self.sio_probe.reset()

        self.status_changed.emit("CONNECTING", f"Connecting to {host}:{port}...")
        self._net_thread = threading.Thread(
            target=self._client_loop, args=(host, int(port)), daemon=True
        )
        self._net_thread.start()
        return True

    def stop(self):
        with self._cond:
            was_active = self._active or self._connected
            self._active = False
            self._connected = False
            sock = self._peer_sock
            lsock = self._listen_sock
            self._peer_sock = None
            self._listen_sock = None
            self._cond.notify_all()

        if sock is not None:
            try:
                self._send_json_raw(sock, {"type": "BYE"})
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

        if lsock is not None:
            try:
                lsock.close()
            except Exception:
                pass

        if self._net_thread is not None and self._net_thread.is_alive():
            if threading.current_thread() is not self._net_thread:
                self._net_thread.join(timeout=1.0)
        self._net_thread = None

        if was_active:
            self.status_changed.emit("OFFLINE", "Disconnected")

    # =========================================================================
    # PACKET TRANSPORT
    # =========================================================================
    def _send_packet(self, sock: socket.socket, kind: bytes, payload: bytes):
        if sock is None:
            return
        frame = kind + payload
        header = struct.pack("!I", len(frame))
        sock.sendall(header + frame)

    def _send_json_raw(self, sock: socket.socket, obj: dict):
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self._send_packet(sock, b"J", data)

    def _send_json(self, obj: dict):
        with self._lock:
            sock = self._peer_sock
        if sock is None:
            return
        try:
            self._send_json_raw(sock, obj)
        except OSError:
            pass

    def send_state_sync(self, raw_state_bytes: bytes):
        if not raw_state_bytes:
            return
        comp = zlib.compress(raw_state_bytes, level=2)
        with self._lock:
            sock = self._peer_sock
        if sock is None:
            return
        try:
            self._send_packet(sock, b"Z", comp)
        except OSError as e:
            self.error_occurred.emit(f"State sync failed: {e}")

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _recv_packet(self, sock: socket.socket) -> tuple[bytes, bytes] | None:
        hdr = self._recv_exact(sock, 4)
        if not hdr:
            return None
        size = struct.unpack("!I", hdr)[0]
        if size <= 0 or size > self.MAX_PACKET_SIZE:
            return None
        body = self._recv_exact(sock, size)
        if not body:
            return None
        return body[:1], body[1:]

    # =========================================================================
    # NETWORK THREAD LOOPS
    # =========================================================================
    def _configure_socket(self, sock: socket.socket):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)

    def _host_loop(self):
        while self._active and self._listen_sock is not None:
            try:
                client_sock, addr = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                self._configure_socket(client_sock)
                with self._lock:
                    self._peer_sock = client_sock
                    pending_state = self._pending_state_bytes

                self._send_json_raw(client_sock, {
                    "type": "HELLO",
                    "mode": self._mode,
                    "frame_delay": self._frame_delay,
                    "coop_role": self._coop_role,
                    "player_name": self._player_name,
                    "game_code": self._local_game_code,
                    "title": self._local_title,
                })

                if self._mode == "lockstep" and pending_state:
                    self.send_state_sync(pending_state)

                with self._cond:
                    self._connected = True
                    # Pre-seed initial delay frames
                    for f in range(self._frame_delay):
                        self._remote_inputs.setdefault(f, 0)
                    self._cond.notify_all()

                self.status_changed.emit("CONNECTED", f"Peer connected from {addr[0]}:{addr[1]}")
                self._read_loop(client_sock)
            except Exception as e:
                if self._active:
                    self.error_occurred.emit(f"Host session error: {e}")
            finally:
                with self._cond:
                    self._connected = False
                    if self._peer_sock is client_sock:
                        self._peer_sock = None
                    self._cond.notify_all()
                try:
                    client_sock.close()
                except Exception:
                    pass
                if self._active:
                    self.status_changed.emit("HOSTING", "Peer disconnected. Waiting for reconnection...")

    def _client_loop(self, host: str, port: int):
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=5.0)
            self._configure_socket(sock)
            with self._lock:
                self._peer_sock = sock

            self._send_json_raw(sock, {
                "type": "HELLO",
                "mode": self._mode,
                "frame_delay": self._frame_delay,
                "player_name": self._player_name,
                "game_code": self._local_game_code,
                "title": self._local_title,
            })

            with self._cond:
                self._connected = True
                for f in range(self._frame_delay):
                    self._remote_inputs.setdefault(f, 0)
                self._cond.notify_all()

            self.status_changed.emit("CONNECTED", f"Connected to {host}:{port}")
            self._read_loop(sock)
        except Exception as e:
            if self._active:
                self.error_occurred.emit(f"Connection failed: {e}")
                self.status_changed.emit("OFFLINE", f"Connection error: {e}")
        finally:
            with self._cond:
                self._active = False
                self._connected = False
                self._peer_sock = None
                self._cond.notify_all()
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _read_loop(self, sock: socket.socket):
        while self._active:
            pkt = self._recv_packet(sock)
            if pkt is None:
                break
            kind, payload = pkt

            if kind == b"Z":
                try:
                    raw = zlib.decompress(payload)
                    with self._cond:
                        self._remote_inputs.clear()
                        for f in range(self._frame_delay):
                            self._remote_inputs[f] = 0
                        self._synced_frames = 0
                        self._cond.notify_all()
                    self.state_sync_received.emit(raw)
                except Exception as e:
                    self.error_occurred.emit(f"Failed to decompress synced state: {e}")
                continue

            if kind != b"J":
                continue

            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue

            mtype = msg.get("type")
            if mtype == "HELLO":
                if not self._is_host:
                    # Adopt host session configuration
                    with self._cond:
                        self._mode = msg.get("mode", self._mode)
                        self._frame_delay = int(msg.get("frame_delay", self._frame_delay))
                        self._coop_role = msg.get("coop_role", self._coop_role)
                        self._remote_inputs.clear()
                        for f in range(self._frame_delay):
                            self._remote_inputs[f] = 0
                        self._cond.notify_all()
                self.peer_identified.emit(
                    str(msg.get("player_name", "Peer")),
                    str(msg.get("game_code", "UNKNOWN")),
                )

            elif mtype == "INPUT":
                f_idx = int(msg.get("f", 0))
                mask = int(msg.get("mask", 0))
                with self._cond:
                    self._remote_inputs[f_idx] = mask
                    self._cond.notify_all()

            elif mtype == "SIO":
                f_idx = int(msg.get("f", 0))
                sio_data = {
                    "siocnt": int(msg.get("siocnt", 0)),
                    "siodata32": int(msg.get("siodata32", 0)),
                    "siomlt_send": int(msg.get("siomlt_send", 0)),
                    "rcnt": int(msg.get("rcnt", 0)),
                    "mode_label": str(msg.get("mode_label", "")),
                }
                with self._cond:
                    self._remote_sio[f_idx] = sio_data
                    self.last_remote_sio = sio_data
                    self._cond.notify_all()

            elif mtype == "PING":
                self._send_json({"type": "PONG", "ts": msg.get("ts", 0)})

            elif mtype == "PONG":
                sent_ts = float(msg.get("ts", 0))
                if sent_ts > 0:
                    rtt = max(0.0, (time.perf_counter() - sent_ts) * 1000.0)
                    self._ping_ms = rtt
                    self.ping_updated.emit(rtt)

            elif mtype == "BYE":
                break

    # =========================================================================
    # PER-FRAME LOCKSTEP & SIO EXCHANGE (CALLED BY EMULATOR WORKER)
    # =========================================================================
    def maybe_send_ping(self):
        now = time.perf_counter()
        if self._connected and (now - self._last_ping_sent) >= 1.5:
            self._last_ping_sent = now
            self._send_json({"type": "PING", "ts": now})

    def exchange_lockstep_input(self, frame_idx: int, local_mask: int, timeout_sec: float = 0.20) -> int | None:
        """
        Sends local input for `frame_idx + frame_delay` and waits for remote input at `frame_idx`.
        Returns combined effective button bitmask, or None if not in active connected lockstep.
        """
        if not self._connected or self._mode != "lockstep":
            return None

        self.maybe_send_ping()
        send_f = frame_idx + self._frame_delay
        self._send_json({"type": "INPUT", "f": send_f, "mask": int(local_mask)})

        deadline = time.perf_counter() + timeout_sec
        remote_mask = None
        with self._cond:
            while self._connected and frame_idx not in self._remote_inputs:
                rem = deadline - time.perf_counter()
                if rem <= 0:
                    break
                self._cond.wait(timeout=rem)
            if frame_idx in self._remote_inputs:
                remote_mask = self._remote_inputs.pop(frame_idx)
                self._synced_frames += 1

        if remote_mask is None:
            return local_mask

        if self._coop_role == "host_only":
            return local_mask if self._is_host else remote_mask
        elif self._coop_role == "swap_p2":
            return remote_mask
        return int(local_mask) | int(remote_mask)

    def exchange_sio_frame(self, frame_idx: int, local_sio: dict, timeout_sec: float = 0.04) -> dict | None:
        """Exchanges GBA SIO register snapshot for frame-level link cable bridging."""
        if not self._connected or self._mode != "link_sio" or not local_sio:
            return None

        self.maybe_send_ping()
        self.last_local_sio = local_sio
        self._send_json({
            "type": "SIO",
            "f": frame_idx,
            "siocnt": local_sio["siocnt"],
            "siodata32": local_sio["siodata32"],
            "siomlt_send": local_sio["siomlt_send"],
            "rcnt": local_sio["rcnt"],
            "mode_label": local_sio.get("mode_label", ""),
        })

        deadline = time.perf_counter() + timeout_sec
        with self._cond:
            while self._connected and frame_idx not in self._remote_sio:
                rem = deadline - time.perf_counter()
                if rem <= 0:
                    break
                self._cond.wait(timeout=rem)
            if frame_idx in self._remote_sio:
                self._synced_frames += 1
                return self._remote_sio.pop(frame_idx)
        return self.last_remote_sio
