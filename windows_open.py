"""Open using Windows' registered verb/DDE, after checking the chosen application."""
import os,re
from pathlib import Path

def registered_command(extension):
    if os.name!='nt':raise OSError('Открытие в Guitar Pro поддерживается на Windows')
    import winreg
    progid=None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,rf'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{extension}\UserChoice') as key:
            progid=winreg.QueryValueEx(key,'ProgId')[0]
    except FileNotFoundError:pass
    if not progid:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,extension) as key:
            progid=winreg.QueryValueEx(key,'')[0]
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,progid+r'\shell\open\command') as key:
        return winreg.QueryValueEx(key,'')[0]

def open_tab(path,exe):
    exe=Path(exe)
    if not exe.is_file():raise ValueError('Guitar Pro не найден. Укажите путь к GuitarPro.exe в настройках.')
    try:command=os.path.expandvars(registered_command(path.suffix.lower()))
    except OSError as ex:
        raise ValueError(f'В Windows не настроено открытие {path.suffix} в Guitar Pro. Назначьте Guitar Pro через «Открыть с помощью».') from ex
    match=re.match(r'^\s*(?:"([^"]+)"|(\S+))',command)
    associated=Path(match.group(1) or match.group(2)) if match else None
    if associated is None or os.path.normcase(str(associated.resolve()))!=os.path.normcase(str(exe.resolve())):
        raise ValueError(f'Для {path.suffix} в Windows выбрана другая программа. Назначьте выбранную Guitar Pro через «Открыть с помощью».')
    os.startfile(str(path),'open')
