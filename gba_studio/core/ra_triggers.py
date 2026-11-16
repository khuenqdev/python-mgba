# ra_triggers.py
import re
import struct


def _bcd_to_dec(val: int) -> int:
    res = 0
    mult = 1
    while val > 0:
        res += (val & 0xF) * mult
        val >>= 4
        mult *= 10
    return res


class GBAMemoryView:
    """
    Maps RetroAchievements GBA addresses to live mGBA EWRAM/IWRAM buffers:
      - RA 0x000000 .. 0x007FFF -> IWRAM (32 KB, GBA 0x03000000)
      - RA 0x008000 .. 0x047FFF -> EWRAM (256 KB, GBA 0x02000000)
    Also accepts direct GBA bus addresses (0x02xxxxxx, 0x03xxxxxx) produced by AddAddress.
    """

    def __init__(self, ewram: bytes = b"", iwram: bytes = b""):
        self.ewram = ewram or b""
        self.iwram = iwram or b""

    def read_bytes(self, addr: int, count: int) -> bytes:
        addr = int(addr) & 0xFFFFFFFF
        if 0x000000 <= addr < 0x008000:
            buf, off = self.iwram, addr
        elif 0x008000 <= addr < 0x048000:
            buf, off = self.ewram, addr - 0x008000
        elif 0x02000000 <= addr <= 0x0203FFFF:
            buf, off = self.ewram, addr - 0x02000000
        elif 0x03000000 <= addr <= 0x03007FFF:
            buf, off = self.iwram, addr - 0x03000000
        else:
            return b"\x00" * count

        if off < 0 or off >= len(buf):
            return b"\x00" * count
        chunk = buf[off:off + count]
        if len(chunk) < count:
            chunk = chunk + (b"\x00" * (count - len(chunk)))
        return chunk

    def read_typed(self, addr: int, size_code: str) -> int | float:
        if size_code == "H":
            return self.read_bytes(addr, 1)[0]
        if size_code == " ":
            return struct.unpack("<H", self.read_bytes(addr, 2))[0]
        if size_code == "X":
            return struct.unpack("<I", self.read_bytes(addr, 4))[0]
        if size_code == "W":
            b = self.read_bytes(addr, 3)
            return b[0] | (b[1] << 8) | (b[2] << 16)
        if size_code == "L":
            return self.read_bytes(addr, 1)[0] & 0x0F
        if size_code == "U":
            return (self.read_bytes(addr, 1)[0] >> 4) & 0x0F
        if size_code == "K":
            return bin(self.read_bytes(addr, 1)[0]).count("1")
        if "M" <= size_code <= "T":
            bit = ord(size_code) - ord("M")
            return (self.read_bytes(addr, 1)[0] >> bit) & 1
        if size_code == "F":
            return struct.unpack("<f", self.read_bytes(addr, 4))[0]
        if size_code == "G":
            return struct.unpack(">H", self.read_bytes(addr, 2))[0]
        if size_code == "I":
            return struct.unpack(">I", self.read_bytes(addr, 4))[0]
        if size_code == "J":
            b = self.read_bytes(addr, 3)
            return (b[0] << 16) | (b[1] << 8) | b[2]
        return self.read_bytes(addr, 1)[0]


class _Operand:
    __slots__ = ("kind", "mode", "size_code", "addr", "const_val", "is_float", "last_val", "prior_val")

    def __init__(self, kind: str, mode: str = "", size_code: str = "H", addr: int = 0, const_val=0, is_float: bool = False):
        self.kind = kind          # "mem" | "const"
        self.mode = mode          # "" | "d" (delta) | "p" (prior) | "b" (bcd) | "~" (invert)
        self.size_code = size_code
        self.addr = addr
        self.const_val = const_val
        self.is_float = is_float
        self.last_val = 0
        self.prior_val = 0


class _Condition:
    __slots__ = ("flag", "lhs", "op", "rhs", "hit_target", "hits", "is_true")

    def __init__(self, flag: str, lhs: _Operand, op: str, rhs: _Operand | None, hit_target: int):
        self.flag = flag
        self.lhs = lhs
        self.op = op
        self.rhs = rhs
        self.hit_target = hit_target
        self.hits = 0
        self.is_true = False


_OP_RE = re.compile(r"(==|!=|<=|>=|<|>|=|\+|-|\*|/|&|\^)")
_MEM_RE = re.compile(r"^([dpb~]?)0x([H XWMLUKFGIJMNOQRST]?)([0-9a-fA-F]+)$")


def _parse_operand(tok: str) -> _Operand:
    tok = tok.strip()
    if not tok:
        return _Operand("const", const_val=0)

    m = _MEM_RE.match(tok)
    if m:
        mode = m.group(1)
        sz = m.group(2) or " "
        addr = int(m.group(3), 16)
        return _Operand("mem", mode=mode, size_code=sz, addr=addr, is_float=(sz == "F"))

    if tok.startswith(("f", "F")) and len(tok) > 1:
        try:
            return _Operand("const", const_val=float(tok[1:]), is_float=True)
        except ValueError:
            pass

    if tok.startswith(("h", "H")) and len(tok) > 1:
        try:
            return _Operand("const", const_val=int(tok[1:], 16))
        except ValueError:
            pass

    if tok.startswith(("v", "V")) and len(tok) > 1:
        tok = tok[1:]

    try:
        if "." in tok:
            return _Operand("const", const_val=float(tok), is_float=True)
        return _Operand("const", const_val=int(tok, 0))
    except ValueError:
        return _Operand("const", const_val=0)


def _split_condition_str(cond_str: str) -> _Condition:
    s = cond_str.strip()
    flag = ""
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        flag = s[0].upper()
        s = s[2:]

    hit_target = 0
    m_hit = re.search(r"(?:\((\d+)\)|\.(\d+)\.)$", s)
    if m_hit:
        hit_target = int(m_hit.group(1) or m_hit.group(2))
        s = s[:m_hit.start()]

    m_op = _OP_RE.search(s)
    if m_op:
        op = m_op.group(1)
        if op == "=":
            op = "=="
        lhs = _parse_operand(s[:m_op.start()])
        rhs = _parse_operand(s[m_op.end():])
    else:
        op = ""
        lhs = _parse_operand(s)
        rhs = None

    return _Condition(flag, lhs, op, rhs, hit_target)


class RATriggerEvaluator:
    """
    Pure-Python rcheevos MemAddr compiler & evaluator for RetroAchievements.
    Supports Core + Alt groups, Delta/Prior, AddSource/SubSource, AddAddress,
    AndNext/OrNext, AddHits/SubHits, ResetIf/ResetNextIf, PauseIf, and Measured progress.
    """

    def __init__(self, mem_addr: str):
        self.mem_addr = (mem_addr or "").strip()
        self.groups = []
        self.measured_value = 0
        self.measured_target = 0
        self.has_measured = False
        self._initialized = False
        self._parse(self.mem_addr)

    def _parse(self, mem_addr: str):
        if not mem_addr:
            return
        raw_groups = mem_addr.split("S")
        for g_str in raw_groups:
            conds = [_split_condition_str(c) for c in g_str.split("_") if c.strip()]
            self.groups.append(conds)

    def reset_hits(self):
        for grp in self.groups:
            for c in grp:
                c.hits = 0

    @staticmethod
    def _eval_operand(opnd: _Operand, mem: GBAMemoryView, addr_offset: int, init_pass: bool):
        if opnd.kind == "const":
            return opnd.const_val

        eff_addr = (opnd.addr + addr_offset) & 0xFFFFFFFF
        cur = mem.read_typed(eff_addr, opnd.size_code)

        if init_pass:
            opnd.last_val = cur
            opnd.prior_val = cur

        if opnd.mode == "d":
            val = opnd.last_val
        elif opnd.mode == "p":
            val = opnd.prior_val
        elif opnd.mode == "b":
            val = _bcd_to_dec(int(cur))
        elif opnd.mode == "~":
            val = (~int(cur)) & 0xFFFFFFFF
        else:
            val = cur

        if not init_pass and cur != opnd.last_val:
            opnd.prior_val = opnd.last_val
        opnd.last_val = cur
        return val

    @staticmethod
    def _apply_cmp_or_arith(lhs, op: str, rhs):
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if op == "/":
            return (lhs / rhs) if rhs != 0 else 0
        if op == "&":
            return int(lhs) & int(rhs)
        if op == "^":
            return int(lhs) ^ int(rhs)
        return bool(lhs)

    def evaluate_frame(self, mem: GBAMemoryView) -> bool:
        """Advances trigger evaluation by 1 frame. Returns True if achievement should fire."""
        if not self.groups:
            return False

        init_pass = not self._initialized
        self._initialized = True

        reset_fired = False
        group_results = []
        measured_if_ok = True

        for grp in self.groups:
            addr_acc = 0
            src_acc = 0
            hits_acc = 0
            chain_and = True
            chain_or = False
            has_chain = False
            pause_active = False
            grp_reset = False
            next_reset = False

            # 1. Check PauseIf first
            temp_addr_acc = 0
            for c in grp:
                if c.flag == "I":
                    temp_addr_acc += int(self._eval_operand(c.lhs, mem, temp_addr_acc, init_pass))
                    continue
                if c.flag == "P":
                    lv = self._eval_operand(c.lhs, mem, temp_addr_acc, init_pass)
                    rv = self._eval_operand(c.rhs, mem, temp_addr_acc, init_pass) if c.rhs else 0
                    res = bool(self._apply_cmp_or_arith(lv, c.op, rv)) if c.op else bool(lv)
                    if c.hit_target > 0:
                        if res and c.hits < c.hit_target:
                            c.hits += 1
                        res = (c.hits >= c.hit_target)
                    if res:
                        pause_active = True
                temp_addr_acc = 0

            if pause_active:
                group_results.append(False)
                continue

            # 2. Evaluate all non-Pause conditions
            grp_pass = True
            for c in grp:
                if c.flag == "P":
                    continue

                if c.flag == "I":
                    lv = self._eval_operand(c.lhs, mem, addr_acc, init_pass)
                    if c.op and c.rhs is not None:
                        rv = self._eval_operand(c.rhs, mem, addr_acc, init_pass)
                        lv = self._apply_cmp_or_arith(lv, c.op, rv)
                    addr_acc = int(lv)
                    continue

                lv = self._eval_operand(c.lhs, mem, addr_acc, init_pass)
                rv = self._eval_operand(c.rhs, mem, addr_acc, init_pass) if c.rhs is not None else 0
                addr_acc = 0

                if c.flag in ("A", "B"):
                    val = self._apply_cmp_or_arith(lv, c.op, rv) if (c.op and c.rhs is not None) else lv
                    src_acc = (src_acc + val) if c.flag == "A" else (src_acc - val)
                    continue

                eff_lv = lv + src_acc
                src_acc = 0

                if c.flag == "M":
                    self.has_measured = True
                    if c.hit_target > 0:
                        self.measured_target = c.hit_target
                    elif c.op and c.rhs is not None and isinstance(rv, (int, float)) and rv > 0:
                        self.measured_value = max(0, int(eff_lv))
                        self.measured_target = int(rv)
                    else:
                        self.measured_value = max(0, int(eff_lv))
                        if self.measured_target <= 0:
                            self.measured_target = 100

                cond_true = bool(self._apply_cmp_or_arith(eff_lv, c.op, rv)) if c.op else bool(eff_lv)

                if has_chain:
                    if not chain_and:
                        cond_true = False
                    if chain_or:
                        cond_true = True
                    chain_and = True
                    chain_or = False
                    has_chain = False

                if c.flag == "N":
                    chain_and = cond_true
                    has_chain = True
                    continue
                if c.flag == "O":
                    chain_or = cond_true
                    has_chain = True
                    continue

                if next_reset:
                    if not cond_true:
                        c.hits = 0
                    next_reset = False

                if c.flag == "Z":
                    next_reset = cond_true
                    continue

                if cond_true:
                    if c.hit_target == 0 or c.hits < c.hit_target or c.flag in ("C", "D"):
                        c.hits += 1

                if c.flag == "C":
                    hits_acc += c.hits
                    continue
                if c.flag == "D":
                    hits_acc -= c.hits
                    continue

                eff_hits = c.hits + hits_acc
                hits_acc = 0

                if c.flag == "M" and c.hit_target > 0:
                    self.measured_value = min(c.hit_target, max(0, eff_hits))

                if c.hit_target > 0:
                    sat = eff_hits >= c.hit_target
                else:
                    sat = cond_true

                if c.flag == "R":
                    if sat:
                        grp_reset = True
                    continue

                if c.flag == "Q":
                    if not sat:
                        measured_if_ok = False
                    continue

                if not sat:
                    grp_pass = False

            if grp_reset:
                reset_fired = True
            group_results.append(grp_pass)

        if not measured_if_ok:
            self.measured_value = 0

        if reset_fired:
            self.reset_hits()
            return False

        if not group_results or not group_results[0]:
            return False

        if len(group_results) > 1:
            return any(group_results[1:])

        return True