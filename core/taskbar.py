import ctypes
from ctypes import wintypes
import uuid


# ============================================================
# Windows Taskbar Progress
# ============================================================

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


def make_guid(value: str):
    """
    Convert string GUID to Windows GUID structure.
    """
    u = uuid.UUID(value)

    return GUID(
        u.time_low,
        u.time_mid,
        u.time_hi_version,
        (wintypes.BYTE * 8)(*u.bytes[8:])
    )


class TaskbarProgress:
    """
    Windows 7+ Taskbar progress indicator.

    Shows progress directly on the application's taskbar icon.
    """

    # ITaskbarList3 progress states
    TBPF_NOPROGRESS = 0x00000000
    TBPF_INDETERMINATE = 0x00000001
    TBPF_NORMAL = 0x00000002
    TBPF_ERROR = 0x00000004
    TBPF_PAUSED = 0x00000008

    # CLSID_TaskbarList
    CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11D0-958A-006097C9A090}"

    # IID_ITaskbarList3
    IID_ITASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"

    CLSCTX_INPROC_SERVER = 0x1

    def __init__(self, hwnd):
        self.hwnd = wintypes.HWND(hwnd)

        self.ole32 = ctypes.windll.ole32

        # Initialize COM for this thread.
        self.ole32.CoInitialize(None)

        self._taskbar = ctypes.c_void_p()

        clsid = make_guid(self.CLSID_TASKBAR_LIST)
        iid = make_guid(self.IID_ITASKBAR_LIST3)

        result = self.ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            self.CLSCTX_INPROC_SERVER,
            ctypes.byref(iid),
            ctypes.byref(self._taskbar),
        )

        if result != 0:
            self._taskbar = None
            self.ole32.CoUninitialize()

            raise OSError(
                f"CoCreateInstance failed: HRESULT 0x{result & 0xFFFFFFFF:08X}"
            )

        # ITaskbarList3::HrInit()
        result = self._hr_init()

        if result != 0:
            self.close()

            raise OSError(
                f"ITaskbarList3::HrInit failed: "
                f"HRESULT 0x{result & 0xFFFFFFFF:08X}"
            )

    # --------------------------------------------------------
    # COM helpers
    # --------------------------------------------------------

    def _get_vtable(self):
        """
        Get COM object's vtable.
        """

        if not self._taskbar:
            raise RuntimeError("Taskbar COM object is not initialized")

        obj = ctypes.cast(
            self._taskbar,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        )

        return obj.contents

    def _get_method(self, index, restype, *argtypes):
        """
        Get COM method from vtable.
        """

        vtable = self._get_vtable()

        prototype = ctypes.WINFUNCTYPE(
            restype,
            ctypes.c_void_p,
            *argtypes
        )

        return prototype(vtable[index])

    # --------------------------------------------------------
    # ITaskbarList3 methods
    # --------------------------------------------------------

    def _hr_init(self):
        """
        ITaskbarList::HrInit()

        vtable index = 3
        """

        method = self._get_method(
            3,
            ctypes.HRESULT,
        )

        return method(self._taskbar)

    def set_progress_state(self, state):
        """
        ITaskbarList3::SetProgressState()

        vtable index = 10
        """

        if not self._taskbar:
            return

        method = self._get_method(
            10,
            ctypes.HRESULT,
            wintypes.HWND,
            ctypes.c_uint,
        )

        return method(
            self._taskbar,
            self.hwnd,
            state,
        )

    def set_progress_value(self, value, maximum):
        """
        ITaskbarList3::SetProgressValue()

        vtable index = 9
        """

        if not self._taskbar:
            return

        method = self._get_method(
            9,
            ctypes.HRESULT,
            wintypes.HWND,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
        )

        return method(
            self._taskbar,
            self.hwnd,
            ctypes.c_ulonglong(max(0, value)),
            ctypes.c_ulonglong(max(1, maximum)),
        )

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def set_progress(self, done, total):
        """
        Set normal progress.

        Example:
            done=50
            total=100

        Result:
            50% progress bar on taskbar icon.
        """

        if total <= 0:
            self.clear()
            return

        done = max(0, min(done, total))

        self.set_progress_state(self.TBPF_NORMAL)
        self.set_progress_value(done, total)

    def indeterminate(self):
        """
        Show animated indeterminate progress.
        """

        self.set_progress_state(
            self.TBPF_INDETERMINATE
        )

    def error(self):
        """
        Show red progress bar.
        """

        self.set_progress_state(
            self.TBPF_ERROR
        )

    def paused(self):
        """
        Show yellow/paused progress bar.
        """

        self.set_progress_state(
            self.TBPF_PAUSED
        )

    def clear(self):
        """
        Remove progress bar from taskbar icon.
        """

        if self._taskbar:
            self.set_progress_state(
                self.TBPF_NOPROGRESS
            )

    def close(self):
        """
        Release COM object.
        """

        if self._taskbar:

            # IUnknown::Release()
            method = self._get_method(
                2,
                ctypes.c_ulong,
            )

            method(self._taskbar)

            self._taskbar = None

        self.ole32.CoUninitialize()