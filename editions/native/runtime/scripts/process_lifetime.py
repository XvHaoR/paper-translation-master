"""Own an API process tree, including Windows venv launchers and workers."""
from __future__ import annotations

import os


class WindowsJob:
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [("user", c.c_int64), ("job", c.c_int64), ("flags", w.DWORD),
                        ("min", c.c_size_t), ("max", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Limits(c.Structure):
            _fields_ = [("basic", Basic), ("io", c.c_uint64 * 6),
                        ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                        ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close()
            raise c.WinError(c.get_last_error())

    def assign(self, process):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            import ctypes
            process.kill()
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        if self.handle:
            import ctypes as c
            from ctypes import wintypes as w
            resume = c.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [w.HANDLE]
            resume.restype = w.LONG
            if resume(int(process._handle)) < 0:
                self.close()
                raise RuntimeError("无法恢复已纳入清理范围的 API 进程")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ParentProcess:
    """Keep the original process handle so PID reuse cannot extend ownership."""
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        if os.name == "nt" and pid:
            import ctypes as c
            from ctypes import wintypes as w
            self.kernel = c.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            self.kernel.OpenProcess.restype = w.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE

    def alive(self):
        if not self.pid:
            return True
        if os.name == "nt":
            return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle, 0) == 258
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
