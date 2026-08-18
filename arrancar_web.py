# -*- coding: utf-8 -*-
"""
===============================================================================
 ARRANCADOR WEB — Sistema de Catas + Tailscale
===============================================================================
 Lanza la app Streamlit bindeada a la IP de TAILSCALE del PC, de modo que el
 servidor solo es accesible desde tus propios dispositivos (red privada y
 cifrada WireGuard), estés en casa o fuera.

 - Si Tailscale está activo:  http://<IP-tailscale>:8501  (móvil, cualquier red)
 - Si Tailscale no está:      respaldo en la red local (0.0.0.0) con aviso.

 Ejecutar:  python arrancar_web.py   (o doble clic en arrancar_web.bat)
===============================================================================
"""

import os
import shutil
import subprocess
import sys

RUTA = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(RUTA, "app_streamlit.py")

# Rutas habituales del CLI de Tailscale en Windows (y en el PATH).
CANDIDATOS_TAILSCALE = [
    "tailscale",
    "tailscale.exe",
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def binario_tailscale():
    """Devuelve la ruta del CLI de Tailscale o None."""
    for c in CANDIDATOS_TAILSCALE:
        if shutil.which(c) or os.path.exists(c):
            return c
    return None


def ip_tailscale() -> str:
    """IP 100.x.y.z del PC en el tailnet; '' si Tailscale no está activo."""
    bin = binario_tailscale()
    if bin is None:
        return ""
    try:
        out = subprocess.run([bin, "ip", "-4"], capture_output=True,
                             text=True, timeout=10)
        ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
        return ip if ip.startswith("100.") else ""
    except Exception:
        return ""


def main():
    ip = ip_tailscale()
    if ip:
        address = ip
        print("=" * 60)
        print("  🌿 SISTEMA DE CATAS — servidor seguro vía Tailscale")
        print("=" * 60)
        print(f"  🔒 IP Tailscale del PC:  {ip}")
        print()
        print("  📱 Desde tu móvil (Tailscale ACTIVO, cualquier red):")
        print(f"     ➜  http://{ip}:8501")
        print()
        print("  Este servidor SOLO es accesible desde tus dispositivos.")
    else:
        address = "0.0.0.0"
        print("=" * 60)
        print("  ⚠️  Tailscale NO está activo en este equipo.")
        print("  Usando la red local como respaldo (0.0.0.0).")
        print(f"  ➜  http://localhost:8501")
        print("  Arranca Tailscale para poder conectarte desde fuera.")
        print("=" * 60)

    print("\n  Pulsa Ctrl+C para detener el servidor.\n")

    cmd = [sys.executable, "-m", "streamlit", "run", APP,
           "--server.address", address, "--server.port", "8501"]
    try:
        subprocess.call(cmd)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
