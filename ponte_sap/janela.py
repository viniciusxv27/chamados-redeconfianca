"""As janelas do app no Windows: achar, capturar (mesmo fora da tela) e entregar clique/teclado.

Tudo por ctypes, sem bibliotecas de fora. A ideia:
- o app roda com a janela FORA da área visível (ninguém vê na área de trabalho);
- ``capturar`` pede ao Windows (DWM) o conteúdo da janela, que continua sendo desenhado;
- ``mouse``/``tecla``/``texto`` mandam mensagens direto para a janela certa (PostMessage),
  sem mexer no cursor do mouse nem roubar o foco do navegador de quem está usando.
"""
import ctypes
import ctypes.wintypes as w
from contextlib import contextmanager

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi')
kernel32 = ctypes.WinDLL('kernel32')

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOMOVE = 0x0004, 0x0010, 0x0001, 0x0002
SW_SHOWNOACTIVATE = 4
DWMWA_EXTENDED_FRAME_BOUNDS = 9
CWP_SKIPINVISIBLE = 0x0001
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000

WM_SETFOCUS, WM_KILLFOCUS, WM_ACTIVATE, WM_NCACTIVATE = 0x0007, 0x0008, 0x0006, 0x0086
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEWHEEL = 0x0204, 0x0205, 0x020A
WM_CLOSE = 0x0010
MK_LBUTTON, MK_RBUTTON, MK_SHIFT, MK_CONTROL = 0x0001, 0x0002, 0x0004, 0x0008
VK_LBUTTON, VK_RBUTTON, VK_SHIFT, VK_CONTROL, VK_MENU = 0x01, 0x02, 0x10, 0x11, 0x12
SMTO_ABORTIFHUNG = 0x0002
ESPERA_MS = 1500            # app ocupado (gravando no SAP): a ponte não trava esperando
WA_ACTIVE = 1

user32.SetProcessDpiAwarenessContext.restype = w.BOOL
try:
    # Coordenadas em pixels reais: sem isso o Windows "estica" tudo em telas com escala > 100%.
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
except (AttributeError, OSError):
    pass

EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, w.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.IsWindowVisible.argtypes = [w.HWND]
user32.IsWindow.argtypes = [w.HWND]
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.ClientToScreen.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
user32.ScreenToClient.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
user32.ChildWindowFromPointEx.argtypes = [w.HWND, w.POINT, w.UINT]
user32.ChildWindowFromPointEx.restype = w.HWND
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
user32.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
user32.GetDC.argtypes = [w.HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindow.argtypes = [w.HWND, w.UINT]
user32.GetWindow.restype = w.HWND
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
user32.IsIconic.argtypes = [w.HWND]
user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
user32.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
user32.SetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
user32.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
kernel32.GetCurrentThreadId.restype = w.DWORD
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]
dwmapi.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', w.DWORD), ('flags', w.DWORD), ('hwndActive', w.HWND), ('hwndFocus', w.HWND),
                ('hwndCapture', w.HWND), ('hwndMenuOwner', w.HWND), ('hwndMoveSize', w.HWND),
                ('hwndCaret', w.HWND), ('rcCaret', w.RECT)]


user32.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GUITHREADINFO)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', w.DWORD), ('biWidth', w.LONG), ('biHeight', w.LONG), ('biPlanes', w.WORD),
                ('biBitCount', w.WORD), ('biCompression', w.DWORD), ('biSizeImage', w.DWORD),
                ('biXPelsPerMeter', w.LONG), ('biYPelsPerMeter', w.LONG), ('biClrUsed', w.DWORD),
                ('biClrImportant', w.DWORD)]


def pid_da_janela(hwnd):
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def thread_da_janela(hwnd):
    return user32.GetWindowThreadProcessId(hwnd, None)


def titulo(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def classe(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def janelas_do_processo(pids):
    """Janelas de topo visíveis dos processos, da que está na frente para a de trás."""
    pids = set(pids)
    achadas = []

    def cada(hwnd, _):
        if user32.IsWindowVisible(hwnd) and pid_da_janela(hwnd) in pids and not user32.IsIconic(hwnd):
            r = retangulo(hwnd)
            if r[2] > 1 and r[3] > 1:
                achadas.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cada), 0)
    return achadas


def retangulo(hwnd):
    """(x, y, largura, altura) da janela inteira, sem a sombra invisível do Windows 10/11."""
    r = w.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def area_cliente(hwnd):
    """(x, y, largura, altura) da área útil (sem barra de título), em coordenadas de tela."""
    r = w.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    p = w.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y, r.right, r.bottom


def mover(hwnd, x, y, largura=None, altura=None):
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    if largura is None:
        flags |= SWP_NOSIZE
        largura = altura = 0
    user32.SetWindowPos(hwnd, None, int(x), int(y), int(largura), int(altura), flags)


def tirar_da_barra_de_tarefas(hwnd):
    """A janela escondida não aparece na barra de tarefas nem no Alt+Tab."""
    estilo = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    novo = (estilo | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if novo != estilo:
        user32.ShowWindow(hwnd, 0)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(novo).value)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def voltar_para_barra_de_tarefas(hwnd):
    estilo = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    novo = (estilo & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
    if novo != estilo:
        user32.ShowWindow(hwnd, 0)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(novo).value)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def capturar(hwnd, so_cliente=True):
    """(largura, altura, bytes BGRA de cima para baixo) do conteúdo da janela, ou None."""
    if so_cliente:
        _x, _y, largura, altura = area_cliente(hwnd)
    else:
        r = w.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        largura, altura = r.right - r.left, r.bottom - r.top
    if largura <= 0 or altura <= 0:
        return None
    tela = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(tela)
    cabecalho = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), largura, -altura, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(cabecalho), 0, ctypes.byref(bits), None, 0)
    antigo = gdi32.SelectObject(dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT | (PW_CLIENTONLY if so_cliente else 0))
        if not ok:
            return None
        dados = ctypes.string_at(bits, largura * altura * 4)
    finally:
        gdi32.SelectObject(dc, antigo)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, tela)
    if not so_cliente:
        # Sem a sombra invisível do Windows 10/11: corta para o retângulo de verdade.
        rx, ry, rw, rh = retangulo(hwnd)
        jx, jy = _janela_xy(hwnd)
        dx, dy = rx - jx, ry - jy
        if (dx, dy, rw, rh) != (0, 0, largura, altura):
            linhas = []
            for linha in range(dy, dy + rh):
                inicio = (linha * largura + dx) * 4
                linhas.append(dados[inicio:inicio + rw * 4])
            dados, largura, altura = b''.join(linhas), rw, rh
    return largura, altura, dados


def retangulo_bruto(hwnd):
    """(x, y, largura, altura) como o SetWindowPos entende (com as bordas invisíveis)."""
    r = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _janela_xy(hwnd):
    r = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top


def _lparam(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def janela_no_ponto(topo, sx, sy):
    """A janela-filha mais funda sob o ponto (coordenadas de tela) e o ponto nas coordenadas dela."""
    atual = topo
    while True:
        p = w.POINT(int(sx), int(sy))
        user32.ScreenToClient(atual, ctypes.byref(p))
        filha = user32.ChildWindowFromPointEx(atual, p, CWP_SKIPINVISIBLE)
        if not filha or filha == atual:
            return atual, p.x, p.y
        atual = filha


@contextmanager
def estado_das_teclas(topo, teclas):
    """Liga a ponte à fila de entrada do app e diz que estas teclas/botões estão apertados ({vk: bool}).

    O Tk (e muito controle do Windows) não confia só na mensagem: confere o estado do botão
    com GetKeyState. Com as filas ligadas (AttachThreadInput), o estado que a ponte define é o
    que o app lê — sem tocar no mouse e no teclado de verdade.
    """
    meu, dele = kernel32.GetCurrentThreadId(), thread_da_janela(topo)
    ligado = bool(dele) and dele != meu and bool(user32.AttachThreadInput(meu, dele, True))
    try:
        estado = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(estado)
        for vk, apertada in teclas.items():
            estado[vk] = 0x80 if apertada else 0
        user32.SetKeyboardState(estado)
        yield
    finally:
        if ligado:
            user32.AttachThreadInput(meu, dele, False)


def enviar(hwnd, msg, wparam, lparam):
    """SendMessage com tempo limite: entregue (e tratada) antes de seguir, mas sem travar se o app estiver ocupado."""
    resultado = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, ESPERA_MS, ctypes.byref(resultado)))


def _modificadores(mods):
    return {VK_SHIFT: bool(mods & 1), VK_CONTROL: bool(mods & 2), VK_MENU: bool(mods & 4)}


def mouse(topo, sx, sy, acao, botao='esquerdo', mods=0, apertado=False):
    """acao: mover | descer | subir | duplo. Mensagens direto na janela, sem mexer no cursor.

    ``apertado``: o botão continua pressionado durante o "mover" (arrastar, selecionar texto).
    """
    alvo, cx, cy = janela_no_ponto(topo, sx, sy)
    direito = botao == 'direito'
    vk, mk = (VK_RBUTTON, MK_RBUTTON) if direito else (VK_LBUTTON, MK_LBUTTON)
    chaves = (MK_SHIFT if mods & 1 else 0) | (MK_CONTROL if mods & 2 else 0)
    if acao == 'mover':
        with estado_das_teclas(topo, {vk: apertado, **_modificadores(mods)}):
            enviar(alvo, WM_MOUSEMOVE, chaves | (mk if apertado else 0), _lparam(cx, cy))
        return alvo
    if direito:
        msg = {'descer': WM_RBUTTONDOWN, 'subir': WM_RBUTTONUP, 'duplo': WM_RBUTTONDOWN}[acao]
    else:
        msg = {'descer': WM_LBUTTONDOWN, 'subir': WM_LBUTTONUP, 'duplo': WM_LBUTTONDBLCLK}[acao]
    descendo = acao != 'subir'
    with estado_das_teclas(topo, {vk: descendo, **_modificadores(mods)}):
        enviar(alvo, WM_MOUSEMOVE, chaves | (mk if descendo else 0), _lparam(cx, cy))
        enviar(alvo, msg, chaves | (mk if descendo else 0), _lparam(cx, cy))
    return alvo


def roda(topo, sx, sy, delta):
    alvo, _cx, _cy = janela_no_ponto(topo, sx, sy)
    enviar(alvo, WM_MOUSEWHEEL, (int(delta) & 0xFFFF) << 16, _lparam(sx, sy))


def ativar(topo):
    """Faz o app achar que está em primeiro plano (Tk só entrega teclado a quem acha que tem o foco)."""
    user32.SendMessageW(topo, WM_NCACTIVATE, 1, 0)
    user32.SendMessageW(topo, WM_ACTIVATE, WA_ACTIVE, 0)
    user32.SendMessageW(topo, WM_SETFOCUS, 0, 0)


def foco_da_thread(hwnd):
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if user32.GetGUIThreadInfo(thread_da_janela(hwnd), ctypes.byref(info)):
        return info.hwndFocus or info.hwndActive
    return None


def _lparam_tecla(vk, subindo):
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    lp = 1 | (scan << 16)
    if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E):   # setas, Home/End, PgUp/Dn, Ins/Del
        lp |= 1 << 24
    if subindo:
        lp |= (1 << 30) | (1 << 31)
    return lp


def e_dialogo(hwnd):
    return classe(hwnd) == '#32770'


def tecla(topo, vk, subindo=False, mods=0):
    """Tecla de controle (Enter, Tab, setas, Backspace, Ctrl+C...) para quem tem o foco no app.

    Caixa de diálogo do Windows (as perguntas "Sim/Não") só trata Enter/Esc/Tab que passam
    pela fila dela: aí a tecla vai por PostMessage. No resto, entrega síncrona com o estado
    das teclas modificadoras ligado.
    """
    msg = WM_KEYUP if subindo else WM_KEYDOWN
    if e_dialogo(topo):
        alvo = foco_da_thread(topo) or topo
        user32.PostMessageW(alvo, msg, vk, _lparam_tecla(vk, subindo))
        return alvo
    with estado_das_teclas(topo, {vk: not subindo, **_modificadores(mods)}):
        alvo = foco_da_thread(topo) or topo
        enviar(alvo, msg, vk, _lparam_tecla(vk, subindo))
    return alvo


def texto(topo, caracteres):
    """Texto digitado (com acento e emoji), caractere por caractere, para quem tem o foco.

    Em caixa de diálogo vai pela fila dela: é assim que a letra de atalho ("N" de &Não) funciona.
    """
    if e_dialogo(topo):
        alvo = foco_da_thread(topo) or topo
        dados = caracteres.encode('utf-16-le')
        for i in range(0, len(dados), 2):
            user32.PostMessageW(alvo, WM_CHAR, int.from_bytes(dados[i:i + 2], 'little'), 1)
        return alvo
    with estado_das_teclas(topo, {}):
        alvo = foco_da_thread(topo) or topo
        dados = caracteres.encode('utf-16-le')
        for i in range(0, len(dados), 2):
            enviar(alvo, WM_CHAR, int.from_bytes(dados[i:i + 2], 'little'), 1)
    return alvo


EnumChildProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user32.EnumChildWindows.argtypes = [w.HWND, EnumChildProc, w.LPARAM]


def filhos(hwnd):
    achados = []
    user32.EnumChildWindows(hwnd, EnumChildProc(lambda h, _: achados.append(h) or True), 0)
    return achados


# ── Processos e o "vigia" das janelas novas ──────────────────────────────────
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD),
                ('szExeFile', w.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [w.HANDLE]


def processos_por_nome(nome):
    """PIDs dos processos com este executável (ex.: AutomacaoSAP.exe)."""
    instantaneo = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not instantaneo or instantaneo == w.HANDLE(-1).value:
        return []
    pids = []
    try:
        entrada = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(instantaneo, ctypes.byref(entrada))
        while ok:
            if entrada.szExeFile.lower() == nome.lower():
                pids.append(entrada.th32ProcessID)
            ok = kernel32.Process32NextW(instantaneo, ctypes.byref(entrada))
    finally:
        kernel32.CloseHandle(instantaneo)
    return pids


EVENT_OBJECT_SHOW = 0x8002
WINEVENT_OUTOFCONTEXT = 0x0000
GA_ROOT = 2
WinEventProc = ctypes.WINFUNCTYPE(None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD)
user32.SetWinEventHook.argtypes = [w.UINT, w.UINT, w.HMODULE, WinEventProc, w.DWORD, w.DWORD, w.UINT]
user32.SetWinEventHook.restype = w.HANDLE
user32.UnhookWinEvent.argtypes = [w.HANDLE]
user32.GetAncestor.argtypes = [w.HWND, w.UINT]
user32.GetAncestor.restype = w.HWND
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]


def vigiar_janelas_novas(pid, ao_aparecer, parar):
    """Chama ``ao_aparecer(hwnd)`` assim que uma janela de topo do processo aparece (antes do próximo quadro
    da tela, na prática): é o que deixa a ponte tirar da área de trabalho as perguntas que o Windows
    joga no meio do monitor. Roda numa thread própria até ``parar`` (threading.Event) ser ligado."""
    import threading

    def callback(_gancho, _evento, hwnd, id_objeto, id_filho, _thread, _tempo):
        if id_objeto == 0 and id_filho == 0 and hwnd and user32.GetAncestor(hwnd, GA_ROOT) == hwnd:
            try:
                ao_aparecer(hwnd)
            except Exception:  # noqa: BLE001 — o gancho nunca pode cair
                pass

    def laco():
        procedimento = WinEventProc(callback)
        gancho = user32.SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, None, procedimento, pid, 0,
                                        WINEVENT_OUTOFCONTEXT)
        meu = kernel32.GetCurrentThreadId()

        def vigiar_parada():
            parar.wait()
            user32.PostThreadMessageW(meu, 0x0012, 0, 0)     # WM_QUIT

        threading.Thread(target=vigiar_parada, daemon=True).start()
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        if gancho:
            user32.UnhookWinEvent(gancho)

    t = threading.Thread(target=laco, daemon=True, name=f'vigia-{pid}')
    t.start()
    return t


def fechar(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def dono(hwnd):
    return user32.GetWindow(hwnd, GW_OWNER)
