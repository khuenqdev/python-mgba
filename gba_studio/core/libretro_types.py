import ctypes

class RetroVariable(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_char_p),
        ("value", ctypes.c_char_p)
    ]

# typedef bool (*retro_set_rumble_state_t)(unsigned port, enum retro_rumble_effect effect, uint16_t strength);
RUMBLE_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_int, ctypes.c_ushort)

class RetroRumbleInterface(ctypes.Structure):
    _fields_ = [
        ("set_rumble_state", RUMBLE_CB_TYPE)
    ]

ENV_CB_TYPE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_uint, ctypes.c_void_p)


