"""Panel de control central del Sistema de Catas.
Muestra el estado del servidor, la URL privada (Tailscale),
la URL publica del tunel Cloudflare y un resumen de los datos."""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime

import customtkinter as ctk

BASE = os.path.dirname(os.path.abspath(__file__))
TAILSCALE_URL = "http://100.67.184.36:8501"
TUNEL_LOG = os.path.join(BASE, "tunel.log")
SERVER_LOG = os.path.join(BASE, "server.log")
JSON_PATH = os.path.join(BASE, "catas.json")
CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"

VERDE = "#3fb96a"
ROJO = "#e8765e"
AMARILLO = "#e8a33d"
GRIS = "#9aa0a6"


def http_status(url=TAILSCALE_URL, timeout=4):
    """Devuelve el codigo HTTP si responde, None si esta caido."""
    try:
        return urllib.request.urlopen(url, timeout=timeout).status
    except Exception:
        return None


def procesos_cloudflared():
    """Devuelve las lineas de tasklist con procesos cloudflared."""
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10).stdout
        return [l for l in out.splitlines() if l.strip().lower().startswith("cloudflared")]
    except Exception:
        return []


def url_publica():
    """Lee la URL del tunel desde tunel.log."""
    try:
        txt = open(TUNEL_LOG, encoding="utf-8", errors="ignore").read()
    except FileNotFoundError:
        return None
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
    return m.group(0) if m else None


def url_responde(url, timeout=8):
    """Comprueba de verdad si la URL publica responde HTTP 200."""
    if not url:
        return False
    try:
        return urllib.request.urlopen(url, timeout=timeout).status == 200
    except Exception:
        return False


def resumen_datos():
    """(catas, productores, perfiles) desde catas.json."""
    try:
        d = json.load(open(JSON_PATH, encoding="utf-8"))
        return (len(d.get("catas", [])), len(d.get("productores", [])), len(d.get("perfiles", [])))
    except Exception:
        return (None, None, None)


def iniciar_servidor():
    subprocess.Popen(
        ["python", "arrancar_web.py"], cwd=BASE,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=open(SERVER_LOG, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
    )


def iniciar_tunel():
    subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--url", TAILSCALE_URL, "--no-autoupdate"], cwd=BASE,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=open(TUNEL_LOG, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
    )


def reiniciar_tunel():
    """Mata el cloudflared zombie y lanza uno nuevo (URL nueva)."""
    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"],
                   capture_output=True, timeout=15)
    time.sleep(2)
    try:
        open(TUNEL_LOG, "w").close()
    except Exception:
        pass
    iniciar_tunel()


def abrir_url(url):
    if url:
        webbrowser.open(url)


class Panel(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Panel de Control · Catas")
        self.geometry("540x430")
        self.minsize(480, 400)
        self.grid_columnconfigure(0, weight=1)

        # Cabecera
        self.lbl_titulo = ctk.CTkLabel(self, text="🌿 Panel de Control · Catas",
                                       font=ctk.CTkFont(size=20, weight="bold"))
        self.lbl_titulo.grid(row=0, column=0, padx=12, pady=(12, 2), sticky="w")
        self.lbl_hora = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), text_color=GRIS)
        self.lbl_hora.grid(row=1, column=0, padx=12, pady=(0, 6), sticky="w")

        # Servidor
        self.f_servidor = ctk.CTkFrame(self)
        self.f_servidor.grid(row=2, column=0, padx=12, pady=4, sticky="ew")
        self.f_servidor.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.f_servidor, text="Servidor Streamlit",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, padx=10, pady=(8, 0), sticky="w")
        self.lbl_servidor_estado = ctk.CTkLabel(self.f_servidor, text="…", font=ctk.CTkFont(size=13))
        self.lbl_servidor_estado.grid(row=0, column=1, padx=10, pady=(8, 0), sticky="e")
        self.lbl_servidor_url = ctk.CTkLabel(self.f_servidor, text=TAILSCALE_URL,
                                             font=ctk.CTkFont(size=13), text_color=VERDE)
        self.lbl_servidor_url.grid(row=1, column=0, columnspan=2, padx=10, pady=(2, 6), sticky="w")
        self.f_servidor_acciones = ctk.CTkFrame(self.f_servidor, fg_color="transparent")
        self.f_servidor_acciones.grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="e")

        # Tunel
        self.f_tunel = ctk.CTkFrame(self)
        self.f_tunel.grid(row=3, column=0, padx=12, pady=4, sticky="ew")
        self.f_tunel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.f_tunel, text="Túnel público (Cloudflare)",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, padx=10, pady=(8, 0), sticky="w")
        self.lbl_tunel_estado = ctk.CTkLabel(self.f_tunel, text="…", font=ctk.CTkFont(size=13))
        self.lbl_tunel_estado.grid(row=0, column=1, padx=10, pady=(8, 0), sticky="e")
        self.lbl_tunel_url = ctk.CTkLabel(self.f_tunel, text="…", font=ctk.CTkFont(size=13),
                                          text_color=VERDE, wraplength=460)
        self.lbl_tunel_url.grid(row=1, column=0, columnspan=2, padx=10, pady=(2, 6), sticky="w")
        self.f_tunel_acciones = ctk.CTkFrame(self.f_tunel, fg_color="transparent")
        self.f_tunel_acciones.grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="e")

        # Datos
        self.f_datos = ctk.CTkFrame(self)
        self.f_datos.grid(row=4, column=0, padx=12, pady=4, sticky="ew")
        self.f_datos.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.f_datos, text="📊 Datos",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, padx=10, pady=(8, 0), sticky="w")
        self.lbl_datos_valor = ctk.CTkLabel(self.f_datos, text="…", font=ctk.CTkFont(size=13))
        self.lbl_datos_valor.grid(row=1, column=0, padx=10, pady=(2, 8), sticky="w")

        # Pie
        self.btn_refrescar = ctk.CTkButton(self, text="🔄 Refrescar", command=self.refrescar, height=38)
        self.btn_refrescar.grid(row=5, column=0, padx=12, pady=(8, 4), sticky="ew")
        self.lbl_nota = ctk.CTkLabel(self,
                                     text="La URL pública cambia en cada reinicio del túnel · IP Tailscale fija: 100.67.184.36",
                                     font=ctk.CTkFont(size=11), text_color=GRIS)
        self.lbl_nota.grid(row=6, column=0, padx=12, pady=(0, 10), sticky="w")

        self.refrescar()
        self.after(20000, self._auto)

    def _auto(self):
        self.refrescar()
        self.after(20000, self._auto)

    def refrescar(self):
        self.btn_refrescar.configure(state="disabled", text="🔄 Comprobando…")
        threading.Thread(target=self._comprobar, daemon=True).start()

    def _comprobar(self):
        st = http_status()
        n_proc = len(procesos_cloudflared())
        url = url_publica()
        ok = url_responde(url) if url else False
        datos = resumen_datos()
        hora = datetime.now().strftime("%H:%M:%S")
        self.after(0, lambda: self._pintar(st, n_proc, url, ok, datos, hora))

    def _pintar(self, st, n_proc, url, ok, datos, hora):
        self.lbl_hora.configure(text=f"Última comprobación: {hora} · se actualiza sola cada 20 s")
        self.btn_refrescar.configure(state="normal", text="🔄 Refrescar")

        if st == 200:
            self.lbl_servidor_estado.configure(text="● En línea · HTTP 200", text_color=VERDE)
            self._acciones_servidor(["abrir", "copiar"])
        else:
            self.lbl_servidor_estado.configure(text="● Caído", text_color=ROJO)
            self._acciones_servidor(["iniciar", "abrir"])

        if n_proc > 0 and url and ok:
            self.lbl_tunel_estado.configure(text=f"● Activo · {n_proc} proceso(s)", text_color=VERDE)
            self.lbl_tunel_url.configure(text=url)
            self._acciones_tunel(["abrir", "copiar"])
        elif n_proc > 0 and url and not ok:
            self.lbl_tunel_estado.configure(text="● Sin respuesta (túnel muerto)", text_color=ROJO)
            self.lbl_tunel_url.configure(text=f"{url}\nEl túnel quick caduca solo: pulsa ▶ Reiniciar túnel para generar una URL nueva.")
            self._acciones_tunel(["reiniciar"])
        elif n_proc > 0:
            self.lbl_tunel_estado.configure(text="● Arrancando…", text_color=AMARILLO)
            self.lbl_tunel_url.configure(text="Esperando a que Cloudflare asigne la URL…")
            self._acciones_tunel([])
        else:
            self.lbl_tunel_estado.configure(text="● Detenido", text_color=ROJO)
            self.lbl_tunel_url.configure(text="— sin URL —")
            self._acciones_tunel(["iniciar"])

        if datos[0] is not None:
            self.lbl_datos_valor.configure(
                text=f"{datos[0]} catas · {datos[1]} productores · {datos[2]} perfiles")
        else:
            self.lbl_datos_valor.configure(text="catas.json no encontrado", text_color=AMARILLO)

    def _acciones_servidor(self, modos):
        for w in self.f_servidor_acciones.winfo_children():
            w.destroy()
        if "abrir" in modos:
            ctk.CTkButton(self.f_servidor_acciones, text="Abrir", width=90, height=32,
                          command=lambda: abrir_url(TAILSCALE_URL)).pack(side="left", padx=4)
        if "copiar" in modos:
            ctk.CTkButton(self.f_servidor_acciones, text="Copiar", width=90, height=32,
                          command=lambda: self._copiar(TAILSCALE_URL)).pack(side="left", padx=4)
        if "iniciar" in modos:
            ctk.CTkButton(self.f_servidor_acciones, text="▶ Iniciar", width=90, height=32,
                          command=lambda: (iniciar_servidor(), self.refrescar())).pack(side="left", padx=4)

    def _acciones_tunel(self, modos):
        for w in self.f_tunel_acciones.winfo_children():
            w.destroy()
        if "abrir" in modos:
            ctk.CTkButton(self.f_tunel_acciones, text="Abrir", width=90, height=32,
                          command=lambda: abrir_url(url_publica())).pack(side="left", padx=4)
        if "copiar" in modos:
            ctk.CTkButton(self.f_tunel_acciones, text="Copiar", width=90, height=32,
                          command=lambda: self._copiar(url_publica())).pack(side="left", padx=4)
        if "iniciar" in modos:
            ctk.CTkButton(self.f_tunel_acciones, text="▶ Iniciar túnel", width=110, height=32,
                          command=lambda: (iniciar_tunel(), self.refrescar())).pack(side="left", padx=4)
        if "reiniciar" in modos:
            ctk.CTkButton(self.f_tunel_acciones, text="▶ Reiniciar túnel", width=130, height=32,
                          command=lambda: (reiniciar_tunel(), self.refrescar())).pack(side="left", padx=4)

    def _copiar(self, texto):
        if not texto:
            return
        self.clipboard_clear()
        self.clipboard_append(texto)
        self.lbl_nota.configure(text="✓ URL copiada al portapapeles")


if __name__ == "__main__":
    Panel().mainloop()
