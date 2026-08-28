# -*- coding: utf-8 -*-
"""生成程序图标 icon.ico（蓝色圆角方块 + 白色锁）"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))


def draw_lock(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64.0
    # 背景圆角方块
    d.rounded_rectangle([8 * s, 8 * s, 56 * s, 56 * s], radius=14 * s, fill=(47, 107, 255, 255))
    # 锁环
    d.arc([22 * s, 12 * s, 42 * s, 32 * s], 180, 360, fill=(255, 255, 255, 255), width=int(5 * s))
    # 锁体
    d.rounded_rectangle([18 * s, 28 * s, 46 * s, 52 * s], radius=5 * s, fill=(255, 255, 255, 255))
    # 钥匙孔
    d.ellipse([28 * s, 36 * s, 36 * s, 44 * s], fill=(47, 107, 255, 255))
    d.rectangle([31 * s, 40 * s, 33 * s, 48 * s], fill=(47, 107, 255, 255))
    return img


def main():
    img = draw_lock(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(os.path.join(BASE, "icon.ico"), format="ICO", sizes=sizes)
    print("icon.ico generated:", os.path.join(BASE, "icon.ico"))


if __name__ == "__main__":
    main()
