#!/usr/bin/env python3
"""
CS2 Triggerbot + УСИЛЕННЫЙ Aimbot (только голова) + Skeleton ESP
- Аимбот: МГНОВЕННОЕ наведение в голову (без плавности, если нужно)
- Настраиваемая скорость и агрессивность
- Skeleton ESP с цветами (враги - красные, свои - зеленые)
"""

import time
import threading
import sys
import math
import ctypes
from ctypes import wintypes
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Key, Listener as KeyboardListener
import pymem
import pymem.process

# Windows API для рисования
import win32gui
import win32con
import win32api

# ========== ОФСЕТЫ ==========
OFFSETS = {
    "dwLocalPlayerPawn": 0x23A9118,
    "dwEntityList": 0x2554050,
    "dwViewMatrix": 0x23AE550,
    "dwViewAngles": 0x23BF1A8,
    
    "m_iHealth": 0x34C,
    "m_iTeamNum": 0x3E7,
    "m_iIDEntIndex": 0x342C,
    "m_vecOrigin": 0x14E4,
    "m_vecViewOffset": 0xF58,
}
ENTITY_IDENTITY_SIZE = 0x70

# ========== НАСТРОЙКИ АИМА (АГРЕССИВНЫЕ) ==========
AIM_FOV = 60            # Большой FOV для поиска целей
AIM_SMOOTH = 3          # МАЛЕНЬКОЕ значение = БЫСТРОЕ наведение (1 = мгновенно)
AIM_SNAP = True         # Мгновенное наведение при FOV < 5°
AIM_RCS = True          # Компенсация отдачи (упрощенная)
AIM_BONE = 0            # 0 = голова

# ========== НАСТРОЙКИ ESP ==========
ESP_ENABLED = True
ESP_DRAW_HEAD = True
ESP_DRAW_SKELETON = True
ESP_DRAW_HEALTH = True
ESP_DRAW_DISTANCE = True

# ========== GLOBALS ==========
pm = None
client = None
running = True
local_pawn = 0
local_team = 0

trigger_on = False
trigger_delay_ms = 100

aim_on = False
aim_fov = AIM_FOV
aim_smooth = AIM_SMOOTH

mouse = MouseController()
overlay_hwnd = None
hdc = None

# ========== ФУНКЦИИ ЧТЕНИЯ ПАМЯТИ ==========
def read_ptr(addr):
    return pm.read_longlong(addr) if addr else 0

def read_int(addr):
    return pm.read_int(addr) if addr else 0

def read_uint8(addr):
    return pm.read_uchar(addr) if addr else 0

def read_float(addr):
    return pm.read_float(addr) if addr else 0.0

def write_float(addr, value):
    pm.write_float(addr, value)

def write_vec3(addr, vec):
    pm.write_float(addr, vec[0])
    pm.write_float(addr + 0x4, vec[1])
    pm.write_float(addr + 0x8, vec[2])

def read_vec3(addr):
    return (read_float(addr), read_float(addr + 0x4), read_float(addr + 0x8))

# ========== МАТЕМАТИКА ДЛЯ АИМА ==========
def vec3_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def vec3_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

def vec3_mul(a, scalar):
    return (a[0] * scalar, a[1] * scalar, a[2] * scalar)

def vec3_len(a):
    return math.sqrt(a[0]**2 + a[1]**2 + a[2]**2)

def vec3_normalize(a):
    length = vec3_len(a)
    if length == 0:
        return (0, 0, 0)
    return (a[0] / length, a[1] / length, a[2] / length)

def calc_angle(local_pos, target_pos):
    """БЫСТРОЕ вычисление углов для наведения"""
    delta = vec3_sub(target_pos, local_pos)
    length = vec3_len(delta)
    
    if length < 0.001:
        return (0, 0)
    
    pitch = -math.asin(delta[2] / length) * (180.0 / math.pi)
    yaw = math.atan2(delta[1], delta[0]) * (180.0 / math.pi)
    
    return (pitch, yaw)

def normalize_angle(angle):
    pitch, yaw = angle
    while yaw > 180:
        yaw -= 360
    while yaw < -180:
        yaw += 360
    return (pitch, yaw)

def clamp_angle(angle):
    pitch, yaw = angle
    pitch = max(-89.0, min(89.0, pitch))
    return (pitch, yaw)

def get_fov(local_angles, target_angles):
    """Быстрое вычисление FOV"""
    delta = normalize_angle((target_angles[0] - local_angles[0], target_angles[1] - local_angles[1]))
    return math.sqrt(delta[0]**2 + delta[1]**2)

def smooth_angle(current, target, smoothing):
    """Плавное наведение (smoothing = 1 = мгновенно)"""
    if smoothing <= 1:
        return target
    delta = normalize_angle((target[0] - current[0], target[1] - current[1]))
    return (current[0] + delta[0] / smoothing, current[1] + delta[1] / smoothing)

# ========== РАБОТА С ЭНТИТИ ==========
def get_entity_from_index(index):
    if index <= 0:
        return 0
    list_ptr = read_ptr(client + OFFSETS["dwEntityList"])
    if not list_ptr:
        return 0
    chunk = index >> 9
    entry_idx = index & 0x1FF
    list_entry = read_ptr(list_ptr + 8 * chunk + 0x10)
    if not list_entry:
        return 0
    return read_ptr(list_entry + ENTITY_IDENTITY_SIZE * entry_idx)

def get_bone_positions(entity):
    """Получает позиции костей для скелетона"""
    origin = read_vec3(entity + OFFSETS["m_vecOrigin"])
    view_offset = read_vec3(entity + OFFSETS["m_vecViewOffset"])
    
    # Если view_offset слишком маленький, используем стандартный
    if view_offset[2] < 10:
        view_offset = (0, 0, 64)
    
    bones = {
        'head': vec3_add(origin, vec3_mul(view_offset, 1.25)),
        'neck': vec3_add(origin, vec3_mul(view_offset, 0.9)),
        'chest': vec3_add(origin, vec3_mul(view_offset, 0.6)),
        'pelvis': vec3_add(origin, vec3_mul(view_offset, -0.1)),
        'left_shoulder': vec3_add(origin, (view_offset[0] - 18, view_offset[1] - 5, view_offset[2] * 0.8)),
        'right_shoulder': vec3_add(origin, (view_offset[0] + 18, view_offset[1] - 5, view_offset[2] * 0.8)),
        'left_elbow': vec3_add(origin, (view_offset[0] - 25, view_offset[1] - 10, view_offset[2] * 0.5)),
        'right_elbow': vec3_add(origin, (view_offset[0] + 25, view_offset[1] - 10, view_offset[2] * 0.5)),
        'left_hand': vec3_add(origin, (view_offset[0] - 30, view_offset[1] - 15, view_offset[2] * 0.3)),
        'right_hand': vec3_add(origin, (view_offset[0] + 30, view_offset[1] - 15, view_offset[2] * 0.3)),
        'left_knee': vec3_add(origin, (view_offset[0] - 10, view_offset[1] - 5, -20)),
        'right_knee': vec3_add(origin, (view_offset[0] + 10, view_offset[1] - 5, -20)),
        'left_foot': vec3_add(origin, (view_offset[0] - 10, view_offset[1] - 5, -40)),
        'right_foot': vec3_add(origin, (view_offset[0] + 10, view_offset[1] - 5, -40)),
    }
    return bones

# ========== WORLD TO SCREEN ==========
def world_to_screen(world_pos, view_matrix):
    """Конвертирует 3D в 2D экранные координаты"""
    w = (view_matrix[3][0] * world_pos[0] +
         view_matrix[3][1] * world_pos[1] +
         view_matrix[3][2] * world_pos[2] +
         view_matrix[3][3])
    
    if w < 0.001:
        return None
    
    x = (view_matrix[0][0] * world_pos[0] +
         view_matrix[0][1] * world_pos[1] +
         view_matrix[0][2] * world_pos[2] +
         view_matrix[0][3]) / w
    
    y = (view_matrix[1][0] * world_pos[0] +
         view_matrix[1][1] * world_pos[1] +
         view_matrix[1][2] * world_pos[2] +
         view_matrix[1][3]) / w
    
    return (x, y)

def read_view_matrix():
    view_matrix_addr = client + OFFSETS["dwViewMatrix"]
    matrix = []
    for i in range(4):
        row = []
        for j in range(4):
            row.append(read_float(view_matrix_addr + (i * 4 + j) * 4))
        matrix.append(row)
    return matrix

# ========== ПОИСК ЦЕЛИ ДЛЯ АИМА (ТОЛЬКО ГОЛОВА) ==========
def get_aim_target():
    local = read_ptr(client + OFFSETS["dwLocalPlayerPawn"])
    if not local:
        return None
    
    local_pos = read_vec3(local + OFFSETS["m_vecOrigin"])
    local_angles = read_vec3(client + OFFSETS["dwViewAngles"])
    
    best_target = None
    best_fov = float('inf')
    best_distance = float('inf')
    
    # Сканируем всех игроков
    for i in range(1, 65):
        entity = get_entity_from_index(i)
        if not entity or entity == local:
            continue
        
        health = read_int(entity + OFFSETS["m_iHealth"])
        if health <= 0 or health > 100:
            continue
        
        team = read_uint8(entity + OFFSETS["m_iTeamNum"])
        if team == 0 or team == local_team:
            continue
        
        # Получаем позицию головы
        head_pos = get_bone_positions(entity)['head']
        target_angles = calc_angle(local_pos, head_pos)
        fov = get_fov(local_angles, target_angles)
        distance = vec3_len(vec3_sub(head_pos, local_pos))
        
        # Выбираем цель с наименьшим FOV
        if fov < best_fov and fov < aim_fov:
            best_fov = fov
            best_distance = distance
            best_target = {
                'entity': entity,
                'head_pos': head_pos,
                'angles': target_angles,
                'fov': fov,
                'distance': distance,
                'health': health
            }
    
    return best_target

# ========== ESP (рисование) ==========
def get_screen_center():
    """Получает центр экрана"""
    return (960, 540)

def draw_text(hdc, x, y, text, color_rgb):
    """Рисует текст на экране"""
    win32gui.SetTextColor(hdc, win32api.RGB(color_rgb[0], color_rgb[1], color_rgb[2]))
    win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
    win32gui.TextOut(hdc, int(x), int(y), text)

def draw_line(hdc, x1, y1, x2, y2, color_rgb, width=2):
    """Рисует линию"""
    pen = win32gui.CreatePen(win32con.PS_SOLID, width, win32api.RGB(color_rgb[0], color_rgb[1], color_rgb[2]))
    old_pen = win32gui.SelectObject(hdc, pen)
    
    win32gui.MoveToEx(hdc, int(x1), int(y1))
    win32gui.LineTo(hdc, int(x2), int(y2))
    
    win32gui.SelectObject(hdc, old_pen)
    win32gui.DeleteObject(pen)

def draw_circle(hdc, x, y, radius, color_rgb):
    """Рисует круг"""
    brush = win32gui.CreateSolidBrush(win32api.RGB(color_rgb[0], color_rgb[1], color_rgb[2]))
    old_brush = win32gui.SelectObject(hdc, brush)
    
    win32gui.Ellipse(hdc, int(x - radius), int(y - radius), int(x + radius), int(y + radius))
    
    win32gui.SelectObject(hdc, old_brush)
    win32gui.DeleteObject(brush)

def draw_esp():
    """Главная функция рисования ESP"""
    global overlay_hwnd
    
    if not overlay_hwnd:
        return
    
    hdc = win32gui.GetDC(overlay_hwnd)
    if not hdc:
        return
    
    # Очищаем экран (делаем прозрачным)
    win32gui.PatBlt(hdc, 0, 0, 1920, 1080, win32con.PATCOPY)
    
    view_matrix = read_view_matrix()
    local = read_ptr(client + OFFSETS["dwLocalPlayerPawn"])
    local_pos = read_vec3(local + OFFSETS["m_vecOrigin"]) if local else (0, 0, 0)
    
    # Сканируем игроков
    for i in range(1, 65):
        entity = get_entity_from_index(i)
        if not entity or entity == local:
            continue
        
        health = read_int(entity + OFFSETS["m_iHealth"])
        if health <= 0 or health > 100:
            continue
        
        team = read_uint8(entity + OFFSETS["m_iTeamNum"])
        if team == 0:
            continue
        
        # Цвет: красный для врагов, зеленый для своих
        color = (255, 0, 0) if team != local_team else (0, 255, 0)
        
        # Получаем кости
        bones = get_bone_positions(entity)
        
        # Рисуем скелетон
        if ESP_DRAW_SKELETON:
            connections = [
                ('head', 'neck'), ('neck', 'chest'), ('chest', 'pelvis'),
                ('neck', 'left_shoulder'), ('neck', 'right_shoulder'),
                ('left_shoulder', 'left_elbow'), ('right_shoulder', 'right_elbow'),
                ('left_elbow', 'left_hand'), ('right_elbow', 'right_hand'),
                ('pelvis', 'left_knee'), ('pelvis', 'right_knee'),
                ('left_knee', 'left_foot'), ('right_knee', 'right_foot'),
            ]
            
            for bone1, bone2 in connections:
                pos1 = bones.get(bone1)
                pos2 = bones.get(bone2)
                
                if pos1 and pos2:
                    screen1 = world_to_screen(pos1, view_matrix)
                    screen2 = world_to_screen(pos2, view_matrix)
                    
                    if screen1 and screen2:
                        x1 = int((screen1[0] + 1) * 960)
                        y1 = int((1 - screen1[1]) * 540)
                        x2 = int((screen2[0] + 1) * 960)
                        y2 = int((1 - screen2[1]) * 540)
                        
                        # Линии тоньше для врагов
                        width = 2 if team != local_team else 1
                        draw_line(hdc, x1, y1, x2, y2, color, width)
        
        # Рисуем голову (круг)
        if ESP_DRAW_HEAD:
            head_screen = world_to_screen(bones['head'], view_matrix)
            if head_screen:
                x = int((head_screen[0] + 1) * 960)
                y = int((1 - head_screen[1]) * 540)
                radius = 6 if team != local_team else 4
                draw_circle(hdc, x, y, radius, color)
        
        # Рисуем здоровье
        if ESP_DRAW_HEALTH:
            head_screen = world_to_screen(bones['head'], view_matrix)
            if head_screen:
                x = int((head_screen[0] + 1) * 960) - 15
                y = int((1 - head_screen[1]) * 540) - 25
                health_text = f"{health}HP"
                draw_text(hdc, x, y, health_text, color)
        
        # Рисуем дистанцию
        if ESP_DRAW_DISTANCE:
            head_screen = world_to_screen(bones['head'], view_matrix)
            if head_screen:
                x = int((head_screen[0] + 1) * 960) - 15
                y = int((1 - head_screen[1]) * 540) - 40
                dist = vec3_len(vec3_sub(bones['head'], local_pos))
                draw_text(hdc, x, y, f"{int(dist)}m", (255, 255, 255))
    
    win32gui.ReleaseDC(overlay_hwnd, hdc)

def create_overlay():
    """Создает оверлей для ESP"""
    global overlay_hwnd
    
    hwnd = win32gui.FindWindow(None, "Counter-Strike 2")
    if not hwnd:
        hwnd = win32gui.FindWindow(None, "cs2")
    
    if not hwnd:
        print("[WARN] CS2 window not found")
        return False
    
    rect = win32gui.GetWindowRect(hwnd)
    
    # Создаем прозрачное окно
    overlay_hwnd = win32gui.CreateWindowEx(
        win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST,
        win32con.WC_DIALOG,
        "ESP Overlay",
        win32con.WS_POPUP,
        rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1],
        None, None, None, None
    )
    
    # Прозрачность
    win32gui.SetLayeredWindowAttributes(overlay_hwnd, 0, 0, win32con.LWA_ALPHA)
    win32gui.ShowWindow(overlay_hwnd, win32con.SW_SHOW)
    win32gui.UpdateWindow(overlay_hwnd)
    
    print("[OK] ESP Overlay created")
    return True

def esp_loop():
    """Цикл ESP"""
    while running:
        try:
            if ESP_ENABLED:
                draw_esp()
        except:
            pass
        time.sleep(0.02)

# ========== TRIGGERBOT ==========
def triggerbot_loop():
    global trigger_on, local_team, trigger_delay_ms
    last_shot = 0
    while running:
        if trigger_on:
            local = read_ptr(client + OFFSETS["dwLocalPlayerPawn"])
            if local:
                idx = read_int(local + OFFSETS["m_iIDEntIndex"])
                if idx > 0:
                    entity = get_entity_from_index(idx)
                    if entity:
                        health = read_int(entity + OFFSETS["m_iHealth"])
                        if health > 0 and health <= 100:
                            team = read_uint8(entity + OFFSETS["m_iTeamNum"])
                            if team != 0 and team != local_team:
                                now = time.time()
                                if now - last_shot >= trigger_delay_ms / 1000.0:
                                    mouse.click(Button.left, 1)
                                    last_shot = now
        time.sleep(0.005)

# ========== AIMBOT (УСИЛЕННЫЙ) ==========
def aimbot_loop():
    global aim_on, local_team, aim_smooth, aim_fov, running
    
    while running:
        if aim_on:
            target = get_aim_target()
            if target:
                angles_addr = client + OFFSETS["dwViewAngles"]
                current_angles = read_vec3(angles_addr)
                
                target_angles = target['angles']
                
                # Если FOV очень маленький - моментальное наведение
                if target['fov'] < 3.0:
                    new_angles = target_angles
                else:
                    new_angles = smooth_angle(current_angles, target_angles, aim_smooth)
                
                new_angles = clamp_angle(new_angles)
                write_vec3(angles_addr, new_angles)
                
                # Авто-огонь при маленьком FOV (если триггербот выключен)
                if target['fov'] < 2.0 and not trigger_on:
                    mouse.click(Button.left, 1)
        
        time.sleep(0.003)  # Максимальная частота обновления

# ========== УПРАВЛЕНИЕ ==========
def on_press(key):
    global trigger_on, aim_on, aim_smooth, aim_fov, running
    
    if key == Key.alt_l:
        trigger_on = not trigger_on
        print(f"[Triggerbot] {'ON' if trigger_on else 'OFF'}")
    
    elif key == Key.ctrl_l:
        aim_on = not aim_on
        print(f"[Aimbot] {'ON' if aim_on else 'OFF'} (FOV={aim_fov}°, Smooth={aim_smooth})")
    
    elif key == Key.up:
        if aim_smooth > 1:
            aim_smooth -= 1
            print(f"[Aimbot] Smooth = {aim_smooth} (меньше = быстрее)")
    
    elif key == Key.down:
        if aim_smooth < 20:
            aim_smooth += 1
            print(f"[Aimbot] Smooth = {aim_smooth}")
    
    elif key == Key.right:
        if aim_fov < 180:
            aim_fov += 10
            print(f"[Aimbot] FOV = {aim_fov}°")
    
    elif key == Key.left:
        if aim_fov > 10:
            aim_fov -= 10
            print(f"[Aimbot] FOV = {aim_fov}°")
    
    elif key == Key.end:
        running = False
        return False

def keyboard_loop():
    with KeyboardListener(on_press=on_press) as listener:
        listener.join()

# ========== MAIN ==========
def main():
    global pm, client, local_team, running, local_pawn
    
    print("\n" + "="*60)
    print("    CS2 TRIGGERBOT + AIMBOT (УСИЛЕННЫЙ) + SKELETON ESP")
    print("="*60)
    print("  Triggerbot:  ALT (toggle)")
    print("  Aimbot:      CTRL (toggle) - АГРЕССИВНЫЙ!")
    print("  ↑/↓:         Smooth (1-20, 1 = мгновенно)")
    print("  ←/→:         FOV (10-180°)")
    print("  END:         Exit")
    print("="*60 + "\n")
    
    try:
        pm = pymem.Pymem("cs2.exe")
        client = pymem.process.module_from_name(pm.process_handle, "client.dll").lpBaseOfDll
        print(f"[OK] Attached to client.dll at 0x{client:X}")
    except Exception as e:
        print(f"[ERROR] Could not attach: {e}")
        input("Press Enter...")
        sys.exit(1)

    pawn_addr = client + OFFSETS["dwLocalPlayerPawn"]
    print("Waiting for local player (join a match)...")
    while running and local_pawn == 0:
        local_pawn = read_ptr(pawn_addr)
        if local_pawn == 0:
            time.sleep(1)
    
    if local_pawn:
        local_team = read_uint8(local_pawn + OFFSETS["m_iTeamNum"])
        print(f"[OK] Local player found, team={local_team}")
    else:
        print("[WARN] Could not find local player")

    try:
        create_overlay()
    except Exception as e:
        print(f"[WARN] ESP error: {e}")

    threading.Thread(target=triggerbot_loop, daemon=True).start()
    threading.Thread(target=aimbot_loop, daemon=True).start()
    threading.Thread(target=keyboard_loop, daemon=True).start()
    
    if overlay_hwnd:
        threading.Thread(target=esp_loop, daemon=True).start()

    print("\n" + "="*60)
    print("  READY!")
    print("  ALT   - Triggerbot")
    print("  CTRL  - Aimbot (БЫСТРЫЙ!)")
    print("  ↑/↓   - Smooth (чем меньше, тем быстрее)")
    print("  ←/→   - FOV")
    print("  END   - Exit")
    print("="*60 + "\n")

    try:
        while running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        running = False

    pm.close_process()
    print("Exited.")

if __name__ == "__main__":
    main()