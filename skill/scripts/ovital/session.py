"""Hidden callback-window transport. Constructing it connects; importing does not.

No SetForegroundWindow, selection messages, mouse, keyboard, launch or network.
Native API rights/bitness/version must be independently checked on the receiver.
"""
import ctypes as C
import os
import struct
import time
from uuid import uuid4

READ_COMMANDS={20006,20012,20016,20100,20101,20801,20802,20302}
WRITE_COMMANDS={20102,20103,20108}

class WinMessageTransport:
    def __init__(self, *, hwnd=None, expected_pid=None, allow_write=False,timeout=5.,response_timeout=10.):
        if os.name!='nt':raise OSError('Ovital native messages require Windows')
        if C.sizeof(C.c_void_p)!=8:raise OSError('this extracted adapter requires 64-bit Python; verify application API/ABI separately')
        from ctypes import wintypes as W
        self.W=W;self.user=C.WinDLL('user32',use_last_error=True);self.kernel=C.WinDLL('kernel32',use_last_error=True)
        U=self.user;K=self.kernel;LRESULT=C.c_ssize_t
        self.allow_write=allow_write;self.timeout=timeout;self.response_timeout=response_timeout;self.received=[];self.log=[];self.window=None;self.app_id=0
        self.PROC=C.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class WindowClass(C.Structure):
            _fields_=[('style',W.UINT),('proc',self.PROC),('clsExtra',C.c_int),('wndExtra',C.c_int),('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
        class CopyData(C.Structure):
            _fields_=[('dwData',C.c_size_t),('cbData',W.DWORD),('lpData',C.c_void_p)]
        self.CopyData=CopyData
        U.FindWindowW.argtypes=[W.LPCWSTR,W.LPCWSTR];U.FindWindowW.restype=W.HWND
        U.GetForegroundWindow.restype=W.HWND
        U.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)];U.GetWindowThreadProcessId.restype=W.DWORD
        U.DefWindowProcW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];U.DefWindowProcW.restype=LRESULT
        U.RegisterClassW.argtypes=[C.POINTER(WindowClass)];U.RegisterClassW.restype=W.ATOM
        U.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p];U.CreateWindowExW.restype=W.HWND
        U.SendMessageTimeoutW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t)];U.SendMessageTimeoutW.restype=LRESULT
        U.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT];U.DispatchMessageW.argtypes=[C.POINTER(W.MSG)];U.DispatchMessageW.restype=LRESULT;U.TranslateMessage.argtypes=[C.POINTER(W.MSG)]
        U.DestroyWindow.argtypes=[W.HWND];U.UnregisterClassW.argtypes=[W.LPCWSTR,W.HINSTANCE]
        K.GetModuleHandleW.argtypes=[W.LPCWSTR];K.GetModuleHandleW.restype=W.HMODULE
        self.foreground_before=int(U.GetForegroundWindow() or 0)
        self.omap=int(hwnd or U.FindWindowW('omapWin',None) or 0)
        if not self.omap:raise RuntimeError('Ovital window not found; no application will be launched')
        pid=W.DWORD();U.GetWindowThreadProcessId(self.omap,C.byref(pid));self.pid=int(pid.value)
        if expected_pid is not None and self.pid!=expected_pid:raise ValueError('Ovital process identity mismatch')
        @self.PROC
        def callback(window,msg,wp,lp):
            if msg==0x4a and lp and wp==1001:
                cd=C.cast(lp,C.POINTER(CopyData)).contents
                if cd.cbData<=64*1024*1024 and (not cd.cbData or cd.lpData):
                    self.received.append((int(cd.dwData)&65535,C.string_at(cd.lpData,cd.cbData) if cd.cbData else b''))
                return 1
            return U.DefWindowProcW(window,msg,wp,lp)
        self.callback=callback;self.class_name='OvitalSkill_'+uuid4().hex;self.instance=K.GetModuleHandleW(None)
        wc=WindowClass();wc.proc=callback;wc.instance=self.instance;wc.name=self.class_name
        if not U.RegisterClassW(C.byref(wc)):raise C.WinError(C.get_last_error())
        try:
            self.window=U.CreateWindowExW(0,self.class_name,'Ovital API callback',0,0,0,0,0,None,None,self.instance,None)
            if not self.window:raise C.WinError(C.get_last_error())
            payload=struct.pack('<Ii20id',0,2,*([0]*20),0.)+self.class_name.encode('utf-16-le')+b'\0\0'
            self.app_id=self.request(20000,payload,expect_response=False)[0]
            if self.app_id<=0:raise RuntimeError('Ovital registration rejected')
        except BaseException:
            self.close();raise

    def request(self,cmd,data=b'', *, expect_response=True,response_timeout=None):
        if cmd!=20000 and cmd not in READ_COMMANDS|WRITE_COMMANDS:raise ValueError('command not exposed by this adapter')
        if cmd in WRITE_COMMANDS and not self.allow_write:raise PermissionError('transport is read-only')
        if cmd==20000 and self.app_id:raise PermissionError('registration cannot be replayed')
        start=len(self.received);buf=C.create_string_buffer(data) if data else None
        cd=self.CopyData((self.app_id<<16)|cmd,len(data),C.cast(buf,C.c_void_p) if buf else None);ret=C.c_size_t()
        ok=self.user.SendMessageTimeoutW(self.omap,0x4a,1001,C.addressof(cd),2,max(1,int(self.timeout*1000)),C.byref(ret))
        signed=C.c_int32(ret.value&0xffffffff).value
        self.log.append(dict(command=cmd,payload_bytes=len(data),send_ok=bool(ok),return_code=signed))
        if not ok:raise TimeoutError('message timed out; effect UNKNOWN, do not replay writes')
        if cmd==20102:
            if -100<=signed<=0:raise RuntimeError('set object rejected: '+str(signed))
        elif signed<0:raise RuntimeError('Ovital rejected command '+str(cmd)+': '+str(signed))
        if not expect_response:return signed,None
        until=time.monotonic()+(self.response_timeout if response_timeout is None else response_timeout);msg=self.W.MSG()
        while time.monotonic()<until:
            found=[payload for command,payload in self.received[start:] if command==cmd]
            if found:return signed,found[-1]
            while self.user.PeekMessageW(C.byref(msg),None,0,0,1):self.user.TranslateMessage(C.byref(msg));self.user.DispatchMessageW(C.byref(msg))
            time.sleep(.01)
        raise TimeoutError('no callback; effect UNKNOWN, do not replay writes')

    def close(self):
        if self.window:self.user.DestroyWindow(self.window);self.window=None
        if getattr(self,'class_name',None):self.user.UnregisterClassW(self.class_name,self.instance)
        self.foreground_after=int(self.user.GetForegroundWindow() or 0)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
