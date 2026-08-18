# -*- coding: utf-8 -*-
"""
===============================================================================
 SISTEMA DE CATAS — REGISTRO Y RANKINGS  ·  v2.0 (Multi-Voto)
===============================================================================
 Aplicación de escritorio premium (CustomTkinter, deep dark + acentos
 dorados/verdes) con sistema MULTI-VOTANTE:

   - Cada persona crea su PERFIL (solo nombre) y puntúa las muestras.
   - Cada cata acumula VOTOS (uno por perfil); la nota de la cata es la MEDIA
     de sus votos -> resultado más fiable.
   - Top GENERAL (media de todos) y Top PERSONAL (por perfil), en secciones
     distintas. Catálogo de PRODUCTORES con foto, renombrado y edición.

 Arquitectura en 3 capas:
   CAPA 1 — DATOS (backend puro): persistencia tolerante + migración v1->v2,
            normalización, coerción numérica y matemática de ponderación.
   CAPA 2 — VISTAS (frontend): VistaFormulario, VistaProductores,
            VistaRankings, VistaPerfiles + modales (detalle, productor, voto).
   CAPA 3 — ORQUESTACIÓN: AppCatas (sidebar, navegación, mediador).

 Ponderaciones (especificación original):
   Visual 25% (Resinoso 40 · Limpieza 35 · Curado 25)
   Aroma  15% (Intensidad 30 · Cuerpo 25 · Limpieza 25 · Curado olor 20)
   Sabor  45% (Perfil 25 · Cantidad 25 · Limpieza boca 20 · Cuerpo 15 · Curado boca 15)
   Efectos 15% (Overall 50 · Potencia 30 · Duración 20)
   Nota Final = Visual*0.25 + Aroma*0.15 + Sabor*0.45 + Efectos*0.15
   (cálculo interno 1-10; mostrado y almacenado sobre 100)

 Datos: C:/Users/Yunes/Desktop/Catas/catas.json   Fotos: ./imagenes/
 Ejecutar:  python app_catas.py
===============================================================================
"""

import json
import os
import shutil
from datetime import datetime

# --- Imports de UI (escritorio) blindados: en la nube (Streamlit Community
# Cloud, Linux sin tkinter) se cargan stubs inertes y el backend puro sigue
# funcionando. messagebox.showerror es parcheado en runtime por la web.
try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    from PIL import Image
except Exception:  # entorno sin GUI (nube / headless)
    class _StubUI:
        def __getattr__(self, name):
            def _inutil(*a, **k):
                if "file" in name or "dir" in name or "save" in name:
                    return ""
                return None
            return _inutil
    ctk = _StubUI()
    filedialog = _StubUI()
    messagebox = _StubUI()
    Image = None

from app_datos import *  # noqa: F401,F403  (backend puro sin tkinter)

def imagen_ctk(ruta, max_w: int, max_h: int):
    """CTkImage con proporción respetada; None si no puede leerse."""
    try:
        im = Image.open(ruta)
        im.thumbnail((max_w, max_h))
        return ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
    except Exception:
        return None


# 3. CAPA DE VISTAS (frontend)
# =============================================================================

class VentanaTexto(ctk.CTkToplevel):
    """Mini-modal genérico para pedir un texto (crear perfil, renombrar...)."""

    def __init__(self, master, titulo: str, etiqueta: str, al_aceptar,
                 valor_inicial: str = "", placeholder: str = ""):
        super().__init__(master)
        self._al_aceptar = al_aceptar
        self.title(titulo)
        self.geometry("380x200")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        ctk.CTkLabel(self, text=etiqueta, font=ctk.CTkFont(size=13), anchor="w",
                     wraplength=340, justify="left").pack(fill="x", padx=20, pady=(18, 8))
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder, height=36,
                                  corner_radius=8)
        self.entry.insert(0, valor_inicial)
        self.entry.pack(fill="x", padx=20)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda _e: self._aceptar())

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkButton(btns, text="Cancelar", width=100, height=34, corner_radius=8,
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(btns, text="Aceptar", width=100, height=34, corner_radius=8,
                      fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                      command=self._aceptar).pack(side="right", padx=(0, 10))
        self.after(10, self._centrar)

    def _centrar(self):
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        x = padre.winfo_x() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_y() + (padre.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _aceptar(self):
        texto = self.entry.get().strip()
        if not texto:
            return
        self._al_aceptar(texto)
        self.destroy()


class PanelVotacion(ctk.CTkScrollableFrame):
    """
    Panel reutilizable de votación: los 4 bloques de sliders (2x2) con sus
    notas en vivo. Lo comparten el formulario de cata y la edición de votos.
    """

    def __init__(self, master, on_cambio=None):
        super().__init__(master, fg_color="transparent")
        self._on_cambio = on_cambio
        self.sliders = {b["clave"]: {} for b in BLOQUES}
        self.val_labels = {b["clave"]: {} for b in BLOQUES}
        self.lbls_bloque_nota = {}
        self.notas_bloques = {b: 0.0 for b in PESOS_GLOBALES}
        self.nota_final = 0.0

        self.grid_columnconfigure(0, weight=1)
        bloques = ctk.CTkFrame(self, fg_color="transparent")
        bloques.grid(row=0, column=0, sticky="ew")
        bloques.grid_columnconfigure((0, 1), weight=1)

        posiciones = [(BLOQUES[0], 0, 0), (BLOQUES[1], 0, 1),
                      (BLOQUES[2], 1, 0), (BLOQUES[3], 1, 1)]
        for meta, fila, col in posiciones:
            self._crear_bloque(bloques, meta, fila, col)

        self.actualizar_nota()

    # ------------------------------------------------------------------ Bloques

    def _crear_bloque(self, parent, meta: dict, fila: int, col: int):
        frame = ctk.CTkFrame(parent, border_width=1, border_color=meta["color"],
                             fg_color=COLOR_TARJETA, corner_radius=10)
        frame.grid(row=fila, column=col, sticky="nsew",
                   padx=(0 if col == 0 else 6, 6 if col == 0 else 0),
                   pady=6, ipadx=12, ipady=8)

        cab = ctk.CTkFrame(frame, fg_color="transparent")
        cab.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(cab, text=str(BLOQUES.index(meta) + 1), width=26, corner_radius=6,
                     fg_color=meta["color"], text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(cab, text=f"{meta['titulo']}  ·  {int(meta['peso'] * 100)}%",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=meta["texto_color"]).pack(side="left")
        self.lbls_bloque_nota[meta["clave"]] = ctk.CTkLabel(
            cab, text="0.0", width=52, corner_radius=6, fg_color=meta["color"],
            text_color="#0e131a", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbls_bloque_nota[meta["clave"]].pack(side="right")

        for clave, etiqueta in meta["subs"]:
            self.crear_slider(frame, etiqueta, meta["clave"], clave)

    def crear_slider(self, parent, texto_label: str, bloque: str, clave: str):
        fila = ctk.CTkFrame(parent, fg_color="transparent")
        fila.pack(fill="x", pady=3)
        ctk.CTkLabel(fila, text=texto_label, width=150, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        val_lbl = ctk.CTkLabel(fila, text="5.0", width=34, font=ctk.CTkFont(weight="bold"),
                               text_color=COLOR_DORADO)
        self.val_labels[bloque][clave] = val_lbl

        def update_lbl(value):
            val = round(float(value), 1)
            val_lbl.configure(text=f"{val:.1f}")
            self.actualizar_nota()

        slider = ctk.CTkSlider(fila, from_=1, to=10, number_of_steps=90,
                               button_color="#D4A373", button_hover_color=COLOR_DORADO_L,
                               progress_color="#555555", command=update_lbl)
        slider.set(5.0)
        slider.pack(side="left", fill="x", expand=True, padx=12)
        val_lbl.pack(side="left")
        self.sliders[bloque][clave] = slider

    # ------------------------------------------------------------------ API

    def obtener_scores(self) -> dict:
        return {b: {c: round(s.get(), 1) for c, s in self.sliders[b].items()}
                for b in self.sliders}

    def actualizar_nota(self):
        """Recalcula y actualiza los chips de nota de bloque; notifica al dueño."""
        self.notas_bloques, self.nota_final = calcular_notas(self.obtener_scores())
        for clave, nota in self.notas_bloques.items():
            self.lbls_bloque_nota[clave].configure(text=f"{nota * 10:.1f}")
        if self._on_cambio:
            self._on_cambio(self.notas_bloques, self.nota_final)

    def set_desde_voto(self, voto):
        """Precarga los sliders desde un voto existente (puntuaciones 1-10)."""
        det = (voto or {}).get("puntuaciones_detalle", {})
        for bloque, sub in det.items():
            for clave, valor in sub.items():
                if clave in self.sliders.get(bloque, {}):
                    self.sliders[bloque][clave].set(_flotante(valor))
                    self.val_labels[bloque][clave].configure(text=f"{_flotante(valor):.1f}")
        self.actualizar_nota()

    def reset(self):
        for bloque in self.sliders:
            for clave, slider in self.sliders[bloque].items():
                slider.set(5.0)
                self.val_labels[bloque][clave].configure(text="5.0")
        self.actualizar_nota()


class VistaFormulario(ctk.CTkScrollableFrame):
    """
    VISTA 1 — Nueva Cata / Votación.
    Elige el PERFIL (quién vota). Si la muestra ya existe, se añade o se
    actualiza el voto de ese perfil (upsert) sin duplicar la cata.
    """

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.foto_path = None
        self.cata_actual = None  # cata existente detectada por nombre

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Nueva Cata", font=ctk.CTkFont(size=32, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Elige quién vota, rellena los datos y puntúa. "
                                "Si la muestra ya existe, se añadirá tu voto.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 14))

        # ---------- Perfil votante ----------
        fila_perfil = ctk.CTkFrame(self, fg_color="transparent")
        fila_perfil.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(fila_perfil, text="👤  Vota como:",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=(0, 10))
        self.combo_perfil = ctk.CTkComboBox(fila_perfil, values=self._nombres_perfiles(),
                                            width=180, height=34, corner_radius=8,
                                            command=lambda _v: self._perfil_cambiado())
        if self._nombres_perfiles():
            self.combo_perfil.set(self._nombres_perfiles()[0])
        self.combo_perfil.pack(side="left", padx=(0, 10))

        ctk.CTkButton(fila_perfil, text="＋ Nuevo perfil", width=120, height=34,
                      corner_radius=8, font=ctk.CTkFont(size=12),
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self._crear_perfil).pack(side="left")

        self.lbl_aviso = ctk.CTkLabel(fila_perfil, text="", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_aviso.pack(side="left", padx=14)

        # ---------- Metadatos ----------
        inputs = ctk.CTkFrame(self, fg_color="transparent")
        inputs.grid(row=3, column=0, sticky="ew", pady=(0, 14))

        self.entry_nombre = ctk.CTkEntry(inputs, placeholder_text="Nombre de la muestra *",
                                         width=210, height=36, corner_radius=8)
        self.entry_nombre.pack(side="left", padx=(0, 12))
        self.entry_nombre.bind("<KeyRelease>", lambda _e: self._detectar_cata_existente())

        ctk.CTkLabel(inputs, text="Productor:", font=ctk.CTkFont(size=13),
                     text_color=COLOR_TEXTO_2).pack(side="left", padx=(0, 6))
        self.combo_productor = ctk.CTkComboBox(inputs, values=self.app.nombres_productores(),
                                               width=190, height=36, corner_radius=8)
        if self.app.nombres_productores():
            self.combo_productor.set(self.app.nombres_productores()[0])
        self.combo_productor.pack(side="left", padx=(0, 8))
        ctk.CTkButton(inputs, text="＋", width=34, height=36, corner_radius=8,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self._crear_productor).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(inputs, text="País:", font=ctk.CTkFont(size=13),
                     text_color=COLOR_TEXTO_2).pack(side="left", padx=(12, 6))
        self.combo_pais = ctk.CTkComboBox(inputs, values=PAISES_VALIDOS, width=130,
                                          height=36, corner_radius=8)
        self.combo_pais.set(PAISES_VALIDOS[0])
        self.combo_pais.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(inputs, text="Tipo:", font=ctk.CTkFont(size=13),
                     text_color=COLOR_TEXTO_2).pack(side="left", padx=(12, 6))
        self.combo_tipo = ctk.CTkComboBox(inputs, values=TIPOS_VALIDOS, width=160,
                                          height=36, corner_radius=8)
        self.combo_tipo.set(TIPOS_VALIDOS[0])
        self.combo_tipo.pack(side="left", padx=(0, 12))

        # ---------- Foto del material ----------
        foto_frame = ctk.CTkFrame(self, fg_color=COLOR_TARJETA, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDE)
        foto_frame.grid(row=4, column=0, sticky="ew", pady=(0, 12))

        self.foto_preview = ctk.CTkLabel(foto_frame, text="📷\nSin foto", width=150,
                                         height=100, fg_color="#101318", corner_radius=8,
                                         font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2)
        self.foto_preview.pack(side="left", padx=(12, 18), pady=12)

        col = ctk.CTkFrame(foto_frame, fg_color="transparent")
        col.pack(side="left", pady=12)
        ctk.CTkLabel(col, text="Foto del material", font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(anchor="w")
        ctk.CTkButton(col, text="📷  Añadir foto", width=150, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                      command=self._seleccionar_foto).pack(anchor="w", pady=(6, 6))
        ctk.CTkButton(col, text="Quitar foto", width=150, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#232B36", hover_color="#2E3846",
                      command=self._quitar_foto).pack(anchor="w")
        self.lbl_foto_nombre = ctk.CTkLabel(col, text="", font=ctk.CTkFont(size=11),
                                            text_color=COLOR_TEXTO_2, anchor="w",
                                            wraplength=240, justify="left")
        self.lbl_foto_nombre.pack(anchor="w", pady=(6, 0))

        # ---------- Bloques 2x2 (panel de votación reutilizable) ----------
        self.panel_votacion = PanelVotacion(self, on_cambio=self._on_nota_cambio)
        self.panel_votacion.grid(row=5, column=0, sticky="nsew", pady=(0, 12))
        # Referencias delegadas (mismos dicts que el panel, para compatibilidad)
        self.sliders = self.panel_votacion.sliders
        self.val_labels = self.panel_votacion.val_labels
        self.lbls_bloque_nota = self.panel_votacion.lbls_bloque_nota

        # ---------- Comentarios ----------
        ctk.CTkLabel(self, text="Comentarios o notas adicionales",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).grid(row=6, column=0, sticky="w", pady=(10, 4))
        self.txt_comentarios = ctk.CTkTextbox(self, height=64, corner_radius=8,
                                              fg_color=COLOR_TARJETA,
                                              border_width=1, border_color=COLOR_BORDE)
        self.txt_comentarios.grid(row=7, column=0, sticky="ew", pady=(0, 10))

        # ---------- Panel inferior ----------
        bottom = ctk.CTkFrame(self, fg_color="#101318", corner_radius=12,
                              border_width=1, border_color=COLOR_BORDE)
        bottom.grid(row=8, column=0, sticky="ew", pady=14, ipadx=20, ipady=14)
        bottom.grid_columnconfigure((0, 1), weight=1)

        nota_panel = ctk.CTkFrame(bottom, fg_color="transparent")
        nota_panel.grid(row=0, column=1, sticky="e", padx=20)
        ctk.CTkLabel(nota_panel, text="TU NOTA", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLOR_TEXTO_2).pack()
        self.lbl_score_final = ctk.CTkLabel(nota_panel, text="0.0 / 100",
                                            font=ctk.CTkFont(size=42, weight="bold"),
                                            text_color=COLOR_DORADO)
        self.lbl_score_final.pack()
        self.barra_final = ctk.CTkProgressBar(nota_panel, width=230, height=10,
                                              progress_color=COLOR_DORADO, fg_color="#2A3240")
        self.barra_final.pack(pady=(2, 0))
        self.barra_final.set(0.5)

        acciones = ctk.CTkFrame(bottom, fg_color="transparent")
        acciones.grid(row=0, column=0, sticky="w", padx=20)
        self.btn_guardar = ctk.CTkButton(acciones, text="💾  Guardar voto",
                                         font=ctk.CTkFont(size=16, weight="bold"),
                                         height=46, corner_radius=10, fg_color=COLOR_VERDE,
                                         hover_color=COLOR_ACTIVO, command=self._guardar)
        self.btn_guardar.pack(side="left", padx=(0, 14))
        ctk.CTkButton(acciones, text="Limpiar", font=ctk.CTkFont(size=14),
                      height=46, corner_radius=10, fg_color="#232B36", hover_color="#2E3846",
                      command=self._limpiar).pack(side="left")
        self.lbl_estado = ctk.CTkLabel(acciones, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_estado.pack(side="left", padx=16)

        self.actualizar_nota_final()

    # ------------------------------------------------------------ Perfiles

    def _nombres_perfiles(self) -> list:
        return [p["nombre"] for p in self.app.datos["perfiles"]]

    def refrescar_perfiles(self):
        """Actualiza el combo de perfiles conservando la selección si existe."""
        nombres = self._nombres_perfiles()
        actual = self.combo_perfil.get()
        self.combo_perfil.configure(values=nombres)
        self.combo_perfil.set(actual if actual in nombres else (nombres[0] if nombres else ""))
        self._perfil_cambiado()

    def _perfil_actual(self):
        nombre = self.combo_perfil.get()
        for p in self.app.datos["perfiles"]:
            if p["nombre"] == nombre:
                return p
        return None

    def _crear_perfil(self):
        def al_aceptar(nombre):
            self.app.crear_perfil(nombre)
            self.refrescar_perfiles()
        VentanaTexto(self, "Nuevo perfil", "Nombre del perfil (p. ej. tu nombre o el de tu amigo):",
                     al_aceptar, placeholder="Nombre")

    def _crear_productor(self):
        """Crea un productor rápido desde el formulario (mismo catálogo)."""
        def al_aceptar(nombre):
            if self.app.crear_productor(nombre):
                self.combo_productor.set(nombre)
        VentanaTexto(self, "Nuevo productor",
                     "Nombre del productor / banco / extractor:",
                     al_aceptar, placeholder="Nombre")

    def refrescar_productores(self):
        """Actualiza el combo de productores conservando la selección si existe."""
        nombres = self.app.nombres_productores()
        actual = self.combo_productor.get()
        self.combo_productor.configure(values=nombres)
        if actual in nombres:
            self.combo_productor.set(actual)
        elif nombres:
            self.combo_productor.set(nombres[0])
        else:
            self.combo_productor.set("")

    def _perfil_cambiado(self):
        """Al cambiar de perfil, recarga los sliders del voto de ese perfil si la cata existe."""
        self._detectar_cata_existente()

    # ------------------------------------------------------------ Cata existente

    def _cata_por_nombre(self, nombre: str):
        nombre = nombre.strip().lower()
        for c in self.app.datos["catas"]:
            if str(c.get("nombre", "")).strip().lower() == nombre:
                return c
        return None

    def _detectar_cata_existente(self):
        """Precarga datos de la cata existente y el voto del perfil actual."""
        nombre = self.entry_nombre.get().strip()
        cata = self._cata_por_nombre(nombre) if nombre else None
        self.cata_actual = cata

        if cata is None:
            self.lbl_aviso.configure(text="", text_color=COLOR_TEXTO_2)
            return

        n_votos = len(votos_validos(cata))
        perfil = self._perfil_actual()
        voto = voto_de_perfil(cata, perfil["id"]) if perfil else None

        if voto is not None:
            self.lbl_aviso.configure(
                text=f"✓ Muestra existente ({n_votos} voto{'s' if n_votos != 1 else ''}) — "
                     f"se ACTUALIZARÁ tu voto", text_color=COLOR_DORADO_L)
        else:
            self.lbl_aviso.configure(
                text=f"✓ Muestra existente ({n_votos} voto{'s' if n_votos != 1 else ''}) — "
                     f"se AÑADIRÁ tu voto", text_color=COLOR_VERDE_L)

        # Precargar metadatos de la cata (sin pisar lo que el usuario ya escribió)
        prod = str(cata.get("productor", "")).strip()
        nombres = self.app.nombres_productores()
        if prod and prod in nombres:
            self.combo_productor.set(prod)
        elif nombres:
            self.combo_productor.set(nombres[0])
        self.combo_pais.set(cata.get("pais") or PAISES_VALIDOS[0])
        self.combo_tipo.set(cata.get("tipo") or TIPOS_VALIDOS[0])
        self.txt_comentarios.delete("1.0", "end")
        self.txt_comentarios.insert("1.0", cata.get("comentarios", ""))

        # Foto de la cata (si no se eligió una nueva en el formulario)
        if not self.foto_path:
            ruta = resolver_ruta_foto(cata.get("foto"))
            img = imagen_ctk(ruta, 150, 100) if ruta else None
            if img is not None:
                self.foto_preview.configure(text="", image=img)
                self.lbl_foto_nombre.configure(text=cata.get("foto", ""))

        # Si el perfil ya votó, precargar sus sliders para editar su voto
        if voto is not None:
            self.panel_votacion.set_desde_voto(voto)

    # ------------------------------------------------------------ Cálculo

    def obtener_scores(self) -> dict:
        return self.panel_votacion.obtener_scores()

    def _on_nota_cambio(self, notas_bloques, nota_final):
        """Actualiza el panel inferior (nota final + barra) desde PanelVotacion."""
        if not hasattr(self, "lbl_score_final") or self.lbl_score_final is None:
            return  # el panel aún se está construyendo
        self.lbl_score_final.configure(text=f"{nota_final * 10:.1f} / 100")
        self.barra_final.set(nota_final / 10.0)

    def actualizar_nota_final(self):
        """Delega el recálculo en vivo al panel de votación."""
        self.panel_votacion.actualizar_nota()

    def _seleccionar_foto(self):
        ruta = filedialog.askopenfilename(
            title="Selecciona una foto del material",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Todos los archivos", "*.*")])
        if not ruta:
            return
        self.foto_path = ruta
        img = imagen_ctk(ruta, 150, 100)
        if img is not None:
            self.foto_preview.configure(text="", image=img)
        else:
            self.foto_preview.configure(text="⚠ No se pudo leer", image=None,
                                        font=ctk.CTkFont(size=12))
        self.lbl_foto_nombre.configure(text=os.path.basename(ruta))

    def _quitar_foto(self):
        self.foto_path = None
        self.foto_preview.configure(text="📷\nSin foto", image=None,
                                    font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2)
        self.lbl_foto_nombre.configure(text="")

    # ------------------------------------------------------------ Cálculo

    def obtener_scores(self) -> dict:
        return {b: {c: round(s.get(), 1) for c, s in self.sliders[b].items()}
                for b in self.sliders}

    def actualizar_nota_final(self):
        notas_bloques, nota_final = calcular_notas(self.obtener_scores())
        for clave, nota in notas_bloques.items():
            self.lbls_bloque_nota[clave].configure(text=f"{nota * 10:.1f}")
        self.lbl_score_final.configure(text=f"{nota_final * 10:.1f} / 100")
        self.barra_final.set(nota_final / 10.0)

    # ------------------------------------------------------------ Guardado

    def _guardar(self):
        nombre = self.entry_nombre.get().strip()
        perfil = self._perfil_actual()
        if not nombre:
            self.lbl_estado.configure(text="⚠ El nombre de la muestra es obligatorio",
                                      text_color="#FF8A65")
            return
        if perfil is None:
            self.lbl_estado.configure(text="⚠ Crea un perfil primero (botón 'Nuevo perfil')",
                                      text_color="#FF8A65")
            return

        score_dict = self.obtener_scores()
        cata = self._cata_por_nombre(nombre)

        if cata is None:
            # Nueva cata
            rid = generar_id({c.get("id") for c in self.app.datos["catas"]})
            foto_rel = self._copiar_foto(rid)
            cata = {
                "id": rid,
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "nombre": nombre,
                "productor": self.combo_productor.get().strip(),
                "pais": self.combo_pais.get(),
                "tipo": self.combo_tipo.get(),
                "comentarios": self.txt_comentarios.get("1.0", "end").strip(),
                "foto": foto_rel,
                "votos": [],
            }
            self.app.datos["catas"].append(cata)
            accion = "nueva"
        else:
            # Cata existente: actualizar metadatos (y foto nueva si se eligió)
            cata["productor"] = self.combo_productor.get().strip() or cata.get("productor", "")
            cata["pais"] = self.combo_pais.get()
            cata["tipo"] = self.combo_tipo.get()
            cata["comentarios"] = self.txt_comentarios.get("1.0", "end").strip()
            if self.foto_path:
                cata["foto"] = self._copiar_foto(cata["id"])
            accion = "existente"

        resultado = upsert_voto(cata, perfil["id"], score_dict)
        guardar_datos(self.app.datos)

        nota = _flotante(voto_de_perfil(cata, perfil["id"]).get("nota_final"))
        self._limpiar()
        if resultado == "nuevo":
            msg = f"✅ Voto de {perfil['nombre']} guardado — {nombre} · {nota:.1f}/100"
        else:
            msg = f"✅ Voto de {perfil['nombre']} ACTUALIZADO — {nombre} · {nota:.1f}/100"
        self.lbl_estado.configure(text=msg, text_color=COLOR_VERDE_L)
        self.app.notificar_cambio()

    def _copiar_foto(self, rid: str) -> str:
        """Copia la foto elegida a ./imagenes/<rid><ext>; '' si no hay o falla."""
        if not self.foto_path:
            return ""
        ext = os.path.splitext(self.foto_path)[1].lower() or ".jpg"
        os.makedirs(RUTA_IMAGENES, exist_ok=True)
        destino = os.path.join(RUTA_IMAGENES, f"{rid}{ext}")
        try:
            shutil.copy2(self.foto_path, destino)
            return f"imagenes/{rid}{ext}"
        except OSError:
            return ""

    def _limpiar(self):
        self.entry_nombre.delete(0, "end")
        nombres = self.app.nombres_productores()
        self.combo_productor.set(nombres[0] if nombres else "")
        self.combo_pais.set(PAISES_VALIDOS[0])
        self.combo_tipo.set(TIPOS_VALIDOS[0])
        self.txt_comentarios.delete("1.0", "end")
        self._quitar_foto()
        self.cata_actual = None
        self.lbl_aviso.configure(text="")
        self.panel_votacion.reset()
        self.lbl_estado.configure(text="")
        self.entry_nombre.focus_set()


class VistaProductores(ctk.CTkScrollableFrame):
    """
    VISTA 2 — Catálogo de PRODUCTORES: todos los productores con su foto,
    nº de catas y nota media. Click -> VentanaProductor (ver catas, renombrar,
    añadir foto).
    """

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Productores", font=ctk.CTkFont(size=32, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Catálogo de productores: créalos aquí y selecciónalos "
                                "desde el formulario de cata. Pincha en uno para ver sus "
                                "catas, renombrarlo, añadirle foto o eliminarlo.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkButton(barra, text="➕  Añadir productor", width=170, height=36,
                      corner_radius=8, font=ctk.CTkFont(size=13, weight="bold"),
                      fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                      command=self._crear).pack(side="left", padx=(0, 14))
        self.entry_buscar = ctk.CTkEntry(barra, placeholder_text="🔍  Buscar productor…",
                                         width=280, height=34, corner_radius=17)
        self.entry_buscar.pack(side="left")
        self.entry_buscar.bind("<KeyRelease>", lambda _e: self.refrescar())
        self.lbl_conteo = ctk.CTkLabel(barra, text="", font=ctk.CTkFont(size=13),
                                       text_color=COLOR_TEXTO_2)
        self.lbl_conteo.pack(side="left", padx=12)

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=3, column=0, sticky="ew")
        self.cuerpo.grid_columnconfigure(0, weight=1)

        self.refrescar()

    def _crear(self):
        def al_aceptar(nombre):
            self.app.crear_productor(nombre)
            self.refrescar()
        VentanaTexto(self, "Nuevo productor",
                     "Nombre del productor / banco / extractor:",
                     al_aceptar, placeholder="Nombre")

    def _productores(self) -> list:
        """Entidades de productores con estadísticas (incluye los sin catas)."""
        lista = []
        for p in self.app.datos["productores"]:
            catas = [c for c in self.app.datos["catas"]
                     if str(c.get("productor", "")).strip() == p["nombre"]]
            medias = [nota_media(c) for c in catas]
            lista.append({"nombre": p["nombre"], "foto": p.get("foto", ""),
                          "catas": catas,
                          "media": round(sum(medias) / len(medias), 1) if medias else 0.0})
        return lista

    def refrescar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

        texto = self.entry_buscar.get().strip().lower()
        productores = [g for g in self._productores()
                       if not texto or texto in g["nombre"].lower()]
        productores.sort(key=lambda g: (-g["media"], g["nombre"].lower()))

        self.lbl_conteo.configure(
            text=f"{len(productores)} productor{'es' if len(productores) != 1 else ''}")

        if not productores:
            ctk.CTkLabel(self.cuerpo, text="Sin productores todavía.",
                         font=ctk.CTkFont(size=14), text_color=COLOR_TEXTO_2,
            ).grid(row=0, column=0, padx=20, pady=30)
            return

        for i, prod in enumerate(productores):
            self._fila_productor(prod, i)

    def _fila_productor(self, prod: dict, indice: int):
        fila = ctk.CTkFrame(self.cuerpo, corner_radius=10, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.grid(row=indice, column=0, sticky="ew", pady=4, padx=2)
        fila.grid_columnconfigure(1, weight=1)
        fila.bind("<Double-Button-1>", lambda _e: self.app.abrir_productor(prod["nombre"]))

        # Foto del productor
        ruta = resolver_ruta_foto(prod["foto"])
        img = imagen_ctk(ruta, 44, 44) if ruta else None
        if img is not None:
            ctk.CTkLabel(fila, text="", image=img, width=44, height=44).grid(
                row=0, column=0, padx=(10, 8), pady=8)
        else:
            ctk.CTkLabel(fila, text="🏭", width=44, height=44, corner_radius=8,
                         fg_color="#101318", font=ctk.CTkFont(size=18)).grid(
                row=0, column=0, padx=(10, 8), pady=8)

        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", padx=6, pady=8)
        ctk.CTkLabel(col, text=prod["nombre"], font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"{len(prod['catas'])} cata{'s' if len(prod['catas']) != 1 else ''}"
                               f" · nota media {prod['media']:.1f}",
                     font=ctk.CTkFont(size=12), text_color=COLOR_TEXTO_2,
                     anchor="w").pack(fill="x")

        ctk.CTkButton(fila, text="Abrir", width=70, height=32, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda n=prod["nombre"]: self.app.abrir_productor(n),
        ).grid(row=0, column=2, padx=(6, 10), pady=8)


class VistaProductos(ctk.CTkScrollableFrame):
    """
    VISTA 2 — Productos: buscador + filtros (tipo y país) sobre todas las
    catas. Doble clic (o 'Abrir') -> VentanaProducto para editar las
    especificaciones y las votaciones de cada perfil.
    """

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Productos", font=ctk.CTkFont(size=32, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Busca cualquier producto, filtra por tipo y país, y "
                                "edita sus especificaciones y votaciones.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        # ---------- Buscador y filtros ----------
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.entry_buscar = ctk.CTkEntry(barra, placeholder_text="🔍  Buscar por nombre o productor…",
                                         width=300, height=36, corner_radius=17)
        self.entry_buscar.pack(side="left", padx=(0, 14))
        self.entry_buscar.bind("<KeyRelease>", lambda _e: self.refrescar())

        ctk.CTkLabel(barra, text="Tipo:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self.combo_filtro_tipo = ctk.CTkComboBox(barra, values=["Todos"] + TIPOS_VALIDOS,
                                                 width=170, height=34,
                                                 command=lambda _v: self.refrescar())
        self.combo_filtro_tipo.set("Todos")
        self.combo_filtro_tipo.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(barra, text="País:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self.combo_filtro_pais = ctk.CTkComboBox(barra, values=["Todos"] + PAISES_VALIDOS,
                                                 width=140, height=34,
                                                 command=lambda _v: self.refrescar())
        self.combo_filtro_pais.set("Todos")
        self.combo_filtro_pais.pack(side="left", padx=(0, 14))

        self.lbl_conteo = ctk.CTkLabel(barra, text="", font=ctk.CTkFont(size=13),
                                       text_color=COLOR_TEXTO_2)
        self.lbl_conteo.pack(side="left", padx=8)

        ctk.CTkButton(barra, text="↺ Reset", width=90, height=34, fg_color="#232B36",
                      hover_color="#2E3846", command=self._reset_filtros).pack(side="right")

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=3, column=0, sticky="ew")
        self.cuerpo.grid_columnconfigure(1, weight=1)

        self.refrescar()

    def _reset_filtros(self):
        self.entry_buscar.delete(0, "end")
        self.combo_filtro_tipo.set("Todos")
        self.combo_filtro_pais.set("Todos")
        self.refrescar()

    def _filtradas(self) -> list:
        texto = self.entry_buscar.get().strip().lower()
        tipo = self.combo_filtro_tipo.get()
        pais = self.combo_filtro_pais.get()
        lista = []
        for c in self.app.datos["catas"]:
            if tipo != "Todos" and c.get("tipo") != tipo:
                continue
            if pais != "Todos" and c.get("pais") != pais:
                continue
            if texto and texto not in (c.get("nombre", "") + " " + c.get("productor", "")).lower():
                continue
            lista.append(c)
        lista.sort(key=lambda c: (-nota_media(c), str(c.get("nombre", "")).lower()))
        return lista

    def refrescar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

        lista = self._filtradas()
        self.lbl_conteo.configure(
            text=f"{len(lista)} producto{'s' if len(lista) != 1 else ''}")

        if not lista:
            ctk.CTkLabel(self.cuerpo, text="Sin productos con esos criterios.",
                         font=ctk.CTkFont(size=14), text_color=COLOR_TEXTO_2,
            ).grid(row=0, column=0, padx=20, pady=30)
            return

        for i, cata in enumerate(lista):
            self._fila(cata, i)

    def _fila(self, cata: dict, indice: int):
        media = nota_media(cata)
        n_votos = len(votos_validos(cata))

        fila = ctk.CTkFrame(self.cuerpo, corner_radius=10, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.grid(row=indice, column=0, sticky="ew", pady=4, padx=2)
        fila.grid_columnconfigure(1, weight=1)
        fila.bind("<Double-Button-1>", lambda _e, c=cata: self.app.abrir_producto(c))

        ruta = resolver_ruta_foto(cata.get("foto"))
        img = imagen_ctk(ruta, 40, 40) if ruta else None
        if img is not None:
            ctk.CTkLabel(fila, text="", image=img, width=40, height=40).grid(
                row=0, column=0, padx=(10, 8), pady=8)
        else:
            ctk.CTkLabel(fila, text="🌿", width=40, height=40, corner_radius=8,
                         fg_color="#101318", font=ctk.CTkFont(size=16)).grid(
                row=0, column=0, padx=(10, 8), pady=8)

        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", padx=6, pady=8)
        ctk.CTkLabel(col, text=cata.get("nombre", "—"), font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"{cata.get('productor', '') or '—'} · {media:.1f} / 100 · "
                               f"{n_votos} voto{'s' if n_votos != 1 else ''}",
                     font=ctk.CTkFont(size=12), text_color=COLOR_TEXTO_2, anchor="w").pack(fill="x")

        pais = cata.get("pais", "")
        ctk.CTkLabel(fila, text=pais or "—", width=90, corner_radius=12,
                     fg_color=COLOR_PAIS.get(pais, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=2, padx=6, pady=8)

        tipo = cata.get("tipo", "—")
        ctk.CTkLabel(fila, text=tipo, width=140, corner_radius=12,
                     fg_color=COLOR_TIPO.get(tipo, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=3, padx=6, pady=8)

        ctk.CTkButton(fila, text="Abrir", width=70, height=32, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda c=cata: self.app.abrir_producto(c),
        ).grid(row=0, column=4, padx=(6, 10), pady=8)


class VentanaVotoEdicion(ctk.CTkToplevel):
    """
    Editor de UN voto: sliders completos precargados con el voto del perfil,
    nota en vivo y guardado (upsert).
    """

    def __init__(self, master, cata: dict, perfil_id: str, nombre_perfil: str,
                 al_guardar):
        super().__init__(master)
        self.title(f"Voto de {nombre_perfil} — {cata.get('nombre', '')}")
        self.geometry("900x760")
        self.minsize(800, 600)
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.after(10, self._centrar)

        ctk.CTkLabel(self, text=f"✏️  Editar voto de {nombre_perfil}",
                     font=ctk.CTkFont(size=20, weight="bold"), anchor="w",
        ).pack(fill="x", padx=24, pady=(16, 0))
        ctk.CTkLabel(self, text=f"{cata.get('nombre', '')} · {cata.get('tipo', '')}",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 10))

        # Panel de sliders reutilizable
        self.panel = PanelVotacion(self, on_cambio=self._nota)
        self.panel.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Resumen de nota en vivo
        resumen = ctk.CTkFrame(self, fg_color="#101318", corner_radius=10,
                               border_width=1, border_color=COLOR_BORDE)
        resumen.pack(fill="x", padx=24, pady=(0, 10), ipadx=16, ipady=8)
        ctk.CTkLabel(resumen, text="NOTA DEL VOTO",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXTO_2).pack(side="left", padx=(4, 16))
        self.lbl_score = ctk.CTkLabel(resumen, text="50.0 / 100",
                                      font=ctk.CTkFont(size=26, weight="bold"),
                                      text_color=COLOR_DORADO)
        self.lbl_score.pack(side="left", padx=(0, 16))
        self.barra = ctk.CTkProgressBar(resumen, width=220, height=10,
                                        progress_color=COLOR_DORADO, fg_color="#2A3240")
        self.barra.pack(side="left")

        # Acciones
        acciones = ctk.CTkFrame(self, fg_color="transparent")
        acciones.pack(fill="x", padx=24, pady=(0, 16))
        ctk.CTkButton(acciones, text="Cancelar", width=120, height=38, corner_radius=8,
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(acciones, text="💾  Guardar voto", width=150, height=38, corner_radius=8,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color=COLOR_VERDE,
                      hover_color=COLOR_ACTIVO, command=self._guardar).pack(side="right",
                                                                            padx=(0, 10))

        # Precargar el voto existente (si lo hay)
        voto = voto_de_perfil(cata, perfil_id)
        if voto is not None:
            self.panel.set_desde_voto(voto)
        else:
            self.panel.actualizar_nota()

    def _nota(self, notas_bloques, nota_final):
        if not hasattr(self, "lbl_score") or self.lbl_score is None:
            return  # el panel aún se está construyendo
        self.lbl_score.configure(text=f"{nota_final * 10:.1f} / 100")
        self.barra.set(nota_final / 10.0)

    def _guardar(self):
        self._al_guardar(self.panel.obtener_scores())
        self.destroy()

    def _centrar(self):
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        x = padre.winfo_x() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_y() + (padre.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class VentanaProducto(ctk.CTkToplevel):
    """
    Ficha completa de un producto: pestaña 'Especificaciones' (editar nombre,
    productor, país, tipo, comentarios y foto) y pestaña 'Votaciones' (ver,
    editar o eliminar el voto de cada perfil, y añadir votos pendientes).
    """

    def __init__(self, master, cata: dict):
        super().__init__(master)
        self.cata = cata  # referencia viva al dict de app.datos["catas"]
        self.tab = "specs"

        self.title(f"Producto — {cata.get('nombre', '')}")
        self.geometry("860x780")
        self.minsize(720, 620)
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.after(10, self._centrar)

        # Cabecera + sub-tabs
        ctk.CTkLabel(self, text=cata.get("nombre", "—"),
                     font=ctk.CTkFont(size=24, weight="bold"), anchor="w",
        ).pack(fill="x", padx=22, pady=(16, 2))

        media = nota_media(cata)
        ctk.CTkLabel(self, text=f"Nota media: {media:.1f} / 100 · "
                                f"{len(votos_validos(cata))} voto"
                                f"{'s' if len(votos_validos(cata)) != 1 else ''}",
                     font=ctk.CTkFont(size=15, weight="bold"), text_color=COLOR_DORADO,
                     anchor="w").pack(fill="x", padx=22, pady=(0, 8))

        tabs = ctk.CTkFrame(self, fg_color="transparent")
        tabs.pack(fill="x", padx=22, pady=(0, 10))
        self.btn_tab_specs = ctk.CTkButton(tabs, text="  📋  Especificaciones  ", height=34,
                                           corner_radius=17, font=ctk.CTkFont(size=13, weight="bold"),
                                           fg_color=COLOR_DORADO, text_color="#0e131a",
                                           command=lambda: self._cambiar_tab("specs"))
        self.btn_tab_specs.pack(side="left", padx=(0, 8))
        self.btn_tab_votos = ctk.CTkButton(tabs, text="  🗳  Votaciones  ", height=34,
                                           corner_radius=17, font=ctk.CTkFont(size=13, weight="bold"),
                                           fg_color="#1A1F26", border_width=1,
                                           border_color=COLOR_BORDE_CHIP, text_color="#B8BEC9",
                                           command=lambda: self._cambiar_tab("votos"))
        self.btn_tab_votos.pack(side="left")

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        self.cuerpo.grid_columnconfigure(0, weight=1)
        self.cuerpo.grid_rowconfigure(0, weight=1)

        self._construir()

    # ------------------------------------------------------------ Tabs

    def _cambiar_tab(self, tab: str):
        self.tab = tab
        if tab == "specs":
            self.btn_tab_specs.configure(fg_color=COLOR_DORADO, text_color="#0e131a",
                                         border_width=0)
            self.btn_tab_votos.configure(fg_color="#1A1F26", text_color="#B8BEC9",
                                         border_width=1, border_color=COLOR_BORDE_CHIP)
        else:
            self.btn_tab_votos.configure(fg_color=COLOR_DORADO, text_color="#0e131a",
                                         border_width=0)
            self.btn_tab_specs.configure(fg_color="#1A1F26", text_color="#B8BEC9",
                                         border_width=1, border_color=COLOR_BORDE_CHIP)
        self._construir()

    def _construir(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()
        if self.tab == "specs":
            self._tab_specs()
        else:
            self._tab_votos()
        # Actualizar el número de votos de la cabecera tras cambios
        media = nota_media(self.cata)
        self._actualizar_cabecera(media)

    def _actualizar_cabecera(self, media: float):
        pass  # la cabecera se reconstruye solo al abrir; los datos viven en self.cata

    # ------------------------------------------------------------ Tab especificaciones

    def _tab_specs(self):
        cont = ctk.CTkScrollableFrame(self.cuerpo, fg_color="transparent")
        cont.grid(row=0, column=0, sticky="nsew")
        cont.grid_columnconfigure(0, weight=1)

        # Foto
        foto_frame = ctk.CTkFrame(cont, fg_color=COLOR_TARJETA, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDE)
        foto_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.foto_preview = ctk.CTkLabel(foto_frame, text="📷\nSin foto", width=150, height=100,
                                         fg_color="#101318", corner_radius=8,
                                         font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2)
        self.foto_preview.pack(side="left", padx=(12, 18), pady=12)
        self._mostrar_foto_actual()

        col = ctk.CTkFrame(foto_frame, fg_color="transparent")
        col.pack(side="left", pady=12)
        ctk.CTkLabel(col, text="Foto del material", font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(anchor="w")
        ctk.CTkButton(col, text="📷  Cambiar foto", width=150, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                      command=self._cambiar_foto).pack(anchor="w", pady=(6, 6))
        ctk.CTkButton(col, text="Quitar foto", width=150, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#232B36", hover_color="#2E3846",
                      command=self._quitar_foto).pack(anchor="w")
        self.lbl_foto_nombre = ctk.CTkLabel(col, text="", font=ctk.CTkFont(size=11),
                                            text_color=COLOR_TEXTO_2, anchor="w",
                                            wraplength=240, justify="left")
        self.lbl_foto_nombre.pack(anchor="w", pady=(6, 0))

        # Campos
        grid = ctk.CTkFrame(cont, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1)

        ctk.CTkLabel(grid, text="Nombre del producto", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.entry_nombre = ctk.CTkEntry(grid, height=36, corner_radius=8)
        self.entry_nombre.insert(0, self.cata.get("nombre", ""))
        self.entry_nombre.grid(row=1, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkLabel(grid, text="Productor", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=0, column=1, sticky="w", pady=(0, 4))
        fila_prod = ctk.CTkFrame(grid, fg_color="transparent")
        fila_prod.grid(row=1, column=1, sticky="ew", padx=(6, 0))
        self.combo_productor = ctk.CTkComboBox(fila_prod, values=self.master.nombres_productores(),
                                               height=36, corner_radius=8)
        prod = str(self.cata.get("productor", "")).strip()
        self.combo_productor.set(prod if prod in self.master.nombres_productores() else
                                 (self.master.nombres_productores()[0]
                                  if self.master.nombres_productores() else ""))
        self.combo_productor.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(fila_prod, text="＋", width=34, height=36, corner_radius=8,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color="#232B36",
                      hover_color="#2E3846", command=self._crear_productor).pack(
            side="left", padx=(6, 0))

        ctk.CTkLabel(grid, text="País de origen", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.combo_pais = ctk.CTkComboBox(grid, values=PAISES_VALIDOS, height=36, corner_radius=8)
        self.combo_pais.set(self.cata.get("pais") or PAISES_VALIDOS[0])
        self.combo_pais.grid(row=3, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkLabel(grid, text="Tipo", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=2, column=1, sticky="w", pady=(10, 4))
        self.combo_tipo = ctk.CTkComboBox(grid, values=TIPOS_VALIDOS, height=36, corner_radius=8)
        self.combo_tipo.set(self.cata.get("tipo") or TIPOS_VALIDOS[0])
        self.combo_tipo.grid(row=3, column=1, sticky="ew", padx=(6, 0))

        ctk.CTkLabel(cont, text="Comentarios", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.txt_comentarios = ctk.CTkTextbox(cont, height=80, corner_radius=8,
                                              fg_color=COLOR_TARJETA,
                                              border_width=1, border_color=COLOR_BORDE)
        self.txt_comentarios.insert("1.0", self.cata.get("comentarios", ""))
        self.txt_comentarios.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        self.lbl_estado = ctk.CTkLabel(cont, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_estado.grid(row=4, column=0, sticky="w", pady=(0, 8))

        ctk.CTkButton(cont, text="💾  Guardar especificaciones", height=42, corner_radius=8,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color=COLOR_VERDE,
                      hover_color=COLOR_ACTIVO, command=self._guardar_specs).grid(
            row=5, column=0, sticky="w")

    def _mostrar_foto_actual(self):
        ruta = resolver_ruta_foto(self.cata.get("foto"))
        img = imagen_ctk(ruta, 150, 100) if ruta else None
        if img is not None:
            self.foto_preview.configure(text="", image=img)
            self.lbl_foto_nombre.configure(text=self.cata.get("foto", ""))

    def _cambiar_foto(self):
        ruta = filedialog.askopenfilename(
            title="Foto del material",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Todos los archivos", "*.*")])
        if not ruta:
            return
        ext = os.path.splitext(ruta)[1].lower() or ".jpg"
        os.makedirs(RUTA_IMAGENES, exist_ok=True)
        destino = os.path.join(RUTA_IMAGENES, f"{self.cata['id']}{ext}")
        try:
            shutil.copy2(ruta, destino)
        except OSError:
            messagebox.showerror("Foto", "No se pudo copiar la imagen.")
            return
        self.cata["foto"] = f"imagenes/{self.cata['id']}{ext}"
        self._mostrar_foto_actual()

    def _quitar_foto(self):
        self.cata["foto"] = ""
        self.foto_preview.configure(text="📷\nSin foto", image=None,
                                    font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2)
        self.lbl_foto_nombre.configure(text="")

    def _crear_productor(self):
        def al_aceptar(nombre):
            if self.master.crear_productor(nombre):
                self.combo_productor.configure(values=self.master.nombres_productores())
                self.combo_productor.set(nombre)
        VentanaTexto(self, "Nuevo productor",
                     "Nombre del productor / banco / extractor:",
                     al_aceptar, placeholder="Nombre")

    def _guardar_specs(self):
        nombre = self.entry_nombre.get().strip()
        if not nombre:
            self.lbl_estado.configure(text="⚠ El nombre no puede estar vacío",
                                      text_color="#FF8A65")
            return
        self.cata["nombre"] = nombre
        self.cata["productor"] = self.combo_productor.get().strip()
        self.cata["pais"] = self.combo_pais.get()
        self.cata["tipo"] = self.combo_tipo.get()
        self.cata["comentarios"] = self.txt_comentarios.get("1.0", "end").strip()
        guardar_datos(self.master.datos)
        self.master.notificar_cambio()
        self.title(f"Producto — {nombre}")
        self.lbl_estado.configure(text="✅ Especificaciones guardadas", text_color=COLOR_VERDE_L)

    # ------------------------------------------------------------ Tab votaciones

    def _tab_votos(self):
        cont = ctk.CTkScrollableFrame(self.cuerpo, fg_color="transparent")
        cont.grid(row=0, column=0, sticky="nsew")
        cont.grid_columnconfigure(0, weight=1)

        # Añadir voto: perfiles que aún no han votado
        pendientes = [p for p in self.master.datos["perfiles"]
                      if voto_de_perfil(self.cata, p["id"]) is None]
        if pendientes:
            fila = ctk.CTkFrame(cont, fg_color="transparent")
            fila.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            ctk.CTkLabel(fila, text="Añadir voto de:", font=ctk.CTkFont(size=13)).pack(
                side="left", padx=(0, 8))
            self.combo_nuevo = ctk.CTkComboBox(fila, values=[p["nombre"] for p in pendientes],
                                               width=180, height=34)
            self.combo_nuevo.set(pendientes[0]["nombre"])
            self.combo_nuevo.pack(side="left", padx=(0, 10))
            ctk.CTkButton(fila, text="➕  Añadir voto", width=130, height=34, corner_radius=8,
                          font=ctk.CTkFont(size=13, weight="bold"), fg_color=COLOR_VERDE,
                          hover_color=COLOR_ACTIVO, command=self._anadir_voto).pack(side="left")
        else:
            ctk.CTkLabel(cont, text="✓ Todos los perfiles han votado este producto.",
                         font=ctk.CTkFont(size=12), text_color=COLOR_VERDE_L, anchor="w",
            ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        # Lista de votos existentes
        votos = votos_validos(self.cata)
        if not votos:
            ctk.CTkLabel(cont, text="Este producto todavía no tiene votos.",
                         font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2,
            ).grid(row=1, column=0, pady=20)
            return

        cab = ctk.CTkFrame(cont, fg_color="transparent")
        cab.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(cab, text="VOTACIONES", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXTO_2).pack(side="left")

        for i, voto in enumerate(votos):
            self._fila_voto(cont, voto, i + 2)

    def _nombre_perfil(self, perfil_id: str) -> str:
        for p in self.master.datos["perfiles"]:
            if p["id"] == perfil_id:
                return p["nombre"]
        return "¿Perfil eliminado?"

    def _fila_voto(self, parent, voto: dict, fila_idx: int):
        nombre = self._nombre_perfil(voto.get("perfil_id"))
        nota = _flotante(voto.get("nota_final"))

        fila = ctk.CTkFrame(parent, corner_radius=8, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.grid(row=fila_idx, column=0, sticky="ew", pady=3)
        fila.grid_columnconfigure(1, weight=1)

        color = "#2E7D32"
        ctk.CTkLabel(fila, text=nombre[:1].upper() or "?", width=34, height=34,
                     corner_radius=17, fg_color=color, text_color="#0e131a",
                     font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=(10, 8), pady=8)

        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", pady=6)
        ctk.CTkLabel(col, text=nombre, font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"Votado el {voto.get('fecha', '—')}",
                     font=ctk.CTkFont(size=11), text_color=COLOR_TEXTO_2, anchor="w").pack(fill="x")

        ctk.CTkLabel(fila, text=f"{nota:.1f}", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=color_nota(nota / 10), width=64, anchor="e").grid(
            row=0, column=2, padx=6, pady=8)
        ctk.CTkButton(fila, text="✏️  Editar", width=80, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda v=voto, n=nombre: self._editar_voto(v, n)).grid(
            row=0, column=3, padx=4, pady=8)
        ctk.CTkButton(fila, text="Desglose", width=86, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda v=voto, n=nombre: VentanaVoto(self, self.cata, v, n)).grid(
            row=0, column=4, padx=4, pady=8)
        ctk.CTkButton(fila, text="🗑", width=36, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#4a2323", hover_color="#6b3030",
                      command=lambda v=voto: self._eliminar_voto(v)).grid(
            row=0, column=5, padx=(0, 10), pady=8)

    def _perfil_por_nombre(self, nombre: str):
        for p in self.master.datos["perfiles"]:
            if p["nombre"] == nombre:
                return p
        return None

    def _anadir_voto(self):
        perfil = self._perfil_por_nombre(self.combo_nuevo.get())
        if perfil is None:
            return
        self._abrir_editor(perfil["id"], perfil["nombre"])

    def _editar_voto(self, voto: dict, nombre_perfil: str):
        self._abrir_editor(voto.get("perfil_id"), nombre_perfil)

    def _abrir_editor(self, perfil_id: str, nombre_perfil: str):
        def al_guardar(scores):
            upsert_voto(self.cata, perfil_id, scores)
            guardar_datos(self.master.datos)
            self.master.notificar_cambio()
            self._construir()
        VentanaVotoEdicion(self, self.cata, perfil_id, nombre_perfil, al_guardar)

    def _eliminar_voto(self, voto: dict):
        nombre = self._nombre_perfil(voto.get("perfil_id"))
        if messagebox.askyesno("Eliminar voto", f"¿Eliminar el voto de {nombre}?"):
            quitar_voto(self.cata, voto.get("perfil_id"))
            guardar_datos(self.master.datos)
            self.master.notificar_cambio()
            self._construir()

    def _centrar(self):
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        x = padre.winfo_x() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_y() + (padre.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class VistaRankings(ctk.CTkFrame):
    """
    VISTA 3 — Rankings en sub-secciones: 'Top General' (media de todos los
    votos) y 'Top Personal' (el top de un perfil concreto). Con filtros de
    tipo, país y búsqueda.
    """

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.modo = "general"     # 'general' | 'personal'
        self.tipo_activo = "Todos"
        self.chips_btns = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(self, text="Rankings", font=ctk.CTkFont(size=32, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Top General (media de todos los votos) o Top Personal "
                                "(solo tus votos).",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        # ---------- Sub-secciones: General | Personal ----------
        subs = ctk.CTkFrame(self, fg_color="transparent")
        subs.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.btn_general = ctk.CTkButton(subs, text="  🌍 Top General  ", height=34,
                                         corner_radius=17, font=ctk.CTkFont(size=13, weight="bold"),
                                         fg_color=COLOR_DORADO, text_color="#0e131a",
                                         command=lambda: self._cambiar_modo("general"))
        self.btn_general.pack(side="left", padx=(0, 8))

        self.btn_personal = ctk.CTkButton(subs, text="  👤 Top Personal  ", height=34,
                                          corner_radius=17, font=ctk.CTkFont(size=13, weight="bold"),
                                          fg_color="#1A1F26", border_width=1,
                                          border_color=COLOR_BORDE_CHIP, text_color="#B8BEC9",
                                          command=lambda: self._cambiar_modo("personal"))
        self.btn_personal.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(subs, text="Perfil:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self.combo_perfil = ctk.CTkComboBox(subs, values=self._nombres_perfiles(),
                                            width=160, height=34, corner_radius=8,
                                            command=lambda _v: self.refrescar())
        if self._nombres_perfiles():
            self.combo_perfil.set(self._nombres_perfiles()[0])
        self.combo_perfil.pack(side="left")

        # ---------- Chips de tipo ----------
        chips_scroll = ctk.CTkScrollableFrame(self, orientation="horizontal", height=42,
                                              fg_color="transparent")
        chips_scroll.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        chip_inner = ctk.CTkFrame(chips_scroll, fg_color="transparent")
        chip_inner.grid(row=0, column=0, sticky="w")

        for tipo in ["Todos"] + TIPOS_VALIDOS:
            btn = ctk.CTkButton(chip_inner, text=f"  {tipo}  ", height=32, corner_radius=16,
                                font=ctk.CTkFont(size=12, weight="bold"),
                                border_width=1, border_color=COLOR_BORDE_CHIP,
                                fg_color="#1A1F26", hover_color="#232B36",
                                text_color="#B8BEC9",
                                command=lambda t=tipo: self._elegir_tipo(t))
            btn.pack(side="left", padx=(0, 8))
            self.chips_btns[tipo] = btn

        # ---------- Filtros ----------
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(barra, text="País:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self.combo_filtro_pais = ctk.CTkComboBox(barra, values=["Todos"] + PAISES_VALIDOS,
                                                 width=140, height=34,
                                                 command=lambda _v: self.refrescar())
        self.combo_filtro_pais.set("Todos")
        self.combo_filtro_pais.pack(side="left", padx=(0, 14))

        self.entry_buscar = ctk.CTkEntry(barra, placeholder_text="🔍  Buscar…",
                                         width=240, height=34, corner_radius=17)
        self.entry_buscar.pack(side="left", padx=(0, 14))
        self.entry_buscar.bind("<KeyRelease>", lambda _e: self.refrescar())

        self.lbl_conteo = ctk.CTkLabel(barra, text="", font=ctk.CTkFont(size=13),
                                       text_color=COLOR_TEXTO_2)
        self.lbl_conteo.pack(side="left", padx=10)

        ctk.CTkButton(barra, text="↺ Reset", width=90, height=34, fg_color="#232B36",
                      hover_color="#2E3846", command=self._reset_filtros).pack(side="right")

        # ---------- Cabecera tabla ----------
        self.cab_lista = ctk.CTkFrame(self, corner_radius=8, fg_color=COLOR_TARJETA,
                                      border_width=1, border_color=COLOR_BORDE)
        self.cab_lista.grid(row=5, column=0, sticky="ew")
        self.cab_lista.grid_columnconfigure(2, weight=1)
        textos = ["", "Foto", "Pos", "Nombre", "País", "Tipo", "Sabor", "Nota Final"]
        anchos = [46, 46, 46, 0, 100, 150, 64, 96]
        for c, (txt, ancho) in enumerate(zip(textos, anchos)):
            lbl = ctk.CTkLabel(self.cab_lista, text=txt, font=ctk.CTkFont(size=12, weight="bold"),
                               text_color=COLOR_TEXTO_2, anchor="w", width=ancho if ancho else 0)
            lbl.grid(row=0, column=c, sticky="ew", padx=(10 if c else 6, 6), pady=8)
            if ancho:
                lbl.grid_propagate(False)

        # ---------- Cuerpo ----------
        self.cuerpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=6, column=0, sticky="nsew", pady=(10, 0))
        self.cuerpo.grid_columnconfigure(2, weight=1)

        self._estilizar_chips()
        self.refrescar()

    # ------------------------------------------------------------ Modo y filtros

    def _nombres_perfiles(self) -> list:
        return [p["nombre"] for p in self.app.datos["perfiles"]]

    def refrescar_perfiles(self):
        nombres = self._nombres_perfiles()
        actual = self.combo_perfil.get()
        self.combo_perfil.configure(values=nombres)
        self.combo_perfil.set(actual if actual in nombres else (nombres[0] if nombres else ""))
        self.refrescar()

    def _cambiar_modo(self, modo: str):
        self.modo = modo
        if modo == "general":
            self.btn_general.configure(fg_color=COLOR_DORADO, text_color="#0e131a",
                                       border_width=0)
            self.btn_personal.configure(fg_color="#1A1F26", text_color="#B8BEC9",
                                        border_width=1, border_color=COLOR_BORDE_CHIP)
        else:
            self.btn_personal.configure(fg_color=COLOR_DORADO, text_color="#0e131a",
                                        border_width=0)
            self.btn_general.configure(fg_color="#1A1F26", text_color="#B8BEC9",
                                       border_width=1, border_color=COLOR_BORDE_CHIP)
        self.refrescar()

    def _elegir_tipo(self, tipo: str):
        self.tipo_activo = tipo
        self._estilizar_chips()
        self.refrescar()

    def _estilizar_chips(self):
        for tipo, btn in self.chips_btns.items():
            if tipo == self.tipo_activo:
                color = COLOR_DORADO if tipo == "Todos" else COLOR_TIPO.get(tipo, COLOR_DORADO)
                btn.configure(fg_color=color, hover_color=color, text_color="#0e131a",
                              border_color=color)
            else:
                btn.configure(fg_color="#1A1F26", hover_color="#232B36",
                              text_color="#B8BEC9", border_color=COLOR_BORDE_CHIP)

    def _reset_filtros(self):
        self.combo_filtro_pais.set("Todos")
        self.entry_buscar.delete(0, "end")
        self._elegir_tipo("Todos")

    def _filtrar(self) -> list:
        """Aplica tipo + país + búsqueda a todas las catas (orden base por media)."""
        pais = self.combo_filtro_pais.get()
        texto = self.entry_buscar.get().strip().lower()
        muestras = []
        for c in self.app.datos["catas"]:
            if self.tipo_activo != "Todos" and c.get("tipo") != self.tipo_activo:
                continue
            if pais != "Todos" and c.get("pais") != pais:
                continue
            if texto and texto not in (c.get("nombre", "") + " " + c.get("productor", "")).lower():
                continue
            muestras.append(c)
        return muestras

    # ------------------------------------------------------------ Render

    def refrescar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

        # Modo personal: sin perfil seleccionado -> aviso
        perfil = None
        if self.modo == "personal":
            for p in self.app.datos["perfiles"]:
                if p["nombre"] == self.combo_perfil.get():
                    perfil = p
            if perfil is None:
                ctk.CTkLabel(self.cuerpo, text="Crea un perfil en la sección 'Perfiles' "
                                               "para ver su top personal.",
                             font=ctk.CTkFont(size=14), text_color=COLOR_TEXTO_2,
                ).grid(row=0, column=0, padx=20, pady=40)
                self.lbl_conteo.configure(text="")
                return

        # Construir lista de (cata, nota, sabor, sub_texto)
        items = []
        for c in self._filtrar():
            if self.modo == "general":
                items.append((c, nota_media(c),
                              _flotante(nota_media_bloques(c).get("sabor")),
                              f"{len(votos_validos(c))} voto{'s' if len(votos_validos(c)) != 1 else ''}",
                              ))
            else:
                voto = voto_de_perfil(c, perfil["id"])
                if voto is None:
                    continue  # el top personal solo muestra catas votadas por el perfil
                media = nota_media(c)
                items.append((c, _flotante(voto.get("nota_final")),
                              _flotante(voto.get("notas_bloques", {}).get("sabor")),
                              f"media {media:.1f} · {len(votos_validos(c))} voto"
                              f"{'s' if len(votos_validos(c)) != 1 else ''}"))

        # Orden robusto: nota desc, sabor desc, nombre asc
        items.sort(key=lambda t: (-t[1], -t[2], str(t[0].get("nombre", "")).lower()))

        etiqueta_modo = "Top General" if self.modo == "general" else f"Top de {perfil['nombre']}"
        filtros = [f"{etiqueta_modo}"]
        if self.tipo_activo != "Todos":
            filtros.append(f"tipo: {self.tipo_activo}")
        if self.combo_filtro_pais.get() != "Todos":
            filtros.append(f"país: {self.combo_filtro_pais.get()}")
        if self.entry_buscar.get().strip():
            filtros.append(f"“{self.entry_buscar.get().strip()}”")
        self.lbl_conteo.configure(text=f"{len(items)} cata{'s' if len(items) != 1 else ''}"
                                       f" · {' · '.join(filtros)}")

        if not items:
            ctk.CTkLabel(self.cuerpo,
                         text="Sin catas con estos criterios." if self.modo == "general"
                         else f"{perfil['nombre']} todavía no ha votado ninguna cata.",
                         font=ctk.CTkFont(size=14), text_color=COLOR_TEXTO_2,
            ).grid(row=0, column=0, padx=20, pady=40)
            return

        for i, (cata, nota, sabor, sub) in enumerate(items, start=1):
            self._fila(cata, i, nota, sabor, sub)

    def _fila(self, cata: dict, posicion: int, nota: float, sabor: float, sub_texto: str):
        fila = ctk.CTkFrame(self.cuerpo, corner_radius=8, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.grid_columnconfigure(2, weight=1)
        fila.bind("<Double-Button-1>", lambda _e, c=cata: self.app.abrir_detalle(c))
        fila.bind("<Enter>", lambda _e, f=fila: f.configure(fg_color=COLOR_TARJETA_HV,
                                                            border_color="#2E3846"))
        fila.bind("<Leave>", lambda _e, f=fila: f.configure(fg_color=COLOR_TARJETA,
                                                            border_color=COLOR_BORDE))

        ruta = resolver_ruta_foto(cata.get("foto"))
        img = imagen_ctk(ruta, 36, 36) if ruta else None
        if img is not None:
            ctk.CTkLabel(fila, text="", image=img, width=36, height=36).grid(
                row=0, column=0, padx=(8, 4), pady=6)
        else:
            ctk.CTkLabel(fila, text="🌿", width=36, height=36, corner_radius=6,
                         fg_color="#101318", font=ctk.CTkFont(size=14)).grid(
                row=0, column=0, padx=(8, 4), pady=6)

        medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
        ctk.CTkLabel(fila, text=f"{medallas.get(posicion, '')}{posicion}º", width=46,
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=1, padx=4, pady=8)

        nom = ctk.CTkFrame(fila, fg_color="transparent")
        nom.grid(row=0, column=2, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(nom, text=cata.get("nombre", "—"), font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(nom, text=f"{cata.get('productor', '') or '—'} · {sub_texto}",
                     font=ctk.CTkFont(size=11), text_color=COLOR_TEXTO_2, anchor="w").pack(fill="x")

        pais = cata.get("pais", "")
        ctk.CTkLabel(fila, text=pais or "—", width=94, corner_radius=12,
                     fg_color=COLOR_PAIS.get(pais, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=3, padx=6, pady=8)

        tipo = cata.get("tipo", "—")
        ctk.CTkLabel(fila, text=tipo, width=140, corner_radius=12,
                     fg_color=COLOR_TIPO.get(tipo, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=4, padx=6, pady=8)

        ctk.CTkLabel(fila, text=f"{sabor:.1f}", width=64, anchor="e",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=color_nota(sabor / 10)).grid(row=0, column=5, sticky="e",
                                                             padx=6, pady=8)

        ctk.CTkLabel(fila, text=f"{nota:.1f}", width=96, anchor="e",
                     font=ctk.CTkFont(size=17, weight="bold"),
                     text_color=COLOR_DORADO).grid(row=0, column=6, sticky="e",
                                                   padx=(6, 12), pady=8)

        ctk.CTkButton(fila, text="Ver", width=48, height=28, font=ctk.CTkFont(size=12),
                      fg_color="#232B36", hover_color="#2E3846",
                      command=lambda c=cata: self.app.abrir_detalle(c),
        ).grid(row=0, column=7, padx=(0, 8), pady=6)


class VistaPerfiles(ctk.CTkScrollableFrame):
    """
    VISTA 4 — Perfiles: cada persona se crea un perfil con su nombre.
    Se puede renombrar o eliminar (sus votos desaparecen).
    """

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Perfiles", font=ctk.CTkFont(size=32, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Cada persona crea su perfil con solo un nombre y puntúa "
                                "las muestras. Sus votos se guardan a su nombre.",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        ctk.CTkButton(self, text="➕  Crear perfil", width=160, height=38, corner_radius=8,
                      font=ctk.CTkFont(size=14, weight="bold"), fg_color=COLOR_VERDE,
                      hover_color=COLOR_ACTIVO, command=self._crear).grid(
            row=2, column=0, sticky="w", pady=(0, 12))

        self.cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=3, column=0, sticky="ew")
        self.cuerpo.grid_columnconfigure(0, weight=1)

        self.refrescar()

    def _crear(self):
        def al_aceptar(nombre):
            self.app.crear_perfil(nombre)
            self.refrescar()
        VentanaTexto(self, "Nuevo perfil", "Nombre del perfil:", al_aceptar,
                     placeholder="Nombre")

    def refrescar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

        perfiles = self.app.datos["perfiles"]
        if not perfiles:
            ctk.CTkLabel(self.cuerpo, text="Todavía no hay perfiles. Crea el primero ↑",
                         font=ctk.CTkFont(size=14), text_color=COLOR_TEXTO_2,
            ).grid(row=0, column=0, padx=20, pady=30)
            return

        for i, perfil in enumerate(perfiles):
            self._fila_perfil(perfil, i)

    def _fila_perfil(self, perfil: dict, indice: int):
        # Estadísticas del perfil
        n_votos = 0
        notas = []
        for c in self.app.datos["catas"]:
            v = voto_de_perfil(c, perfil["id"])
            if v is not None and v.get("puntuaciones_detalle"):
                n_votos += 1
                notas.append(_flotante(v.get("nota_final")))
        media = round(sum(notas) / len(notas), 1) if notas else 0.0

        fila = ctk.CTkFrame(self.cuerpo, corner_radius=10, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.grid(row=indice, column=0, sticky="ew", pady=4, padx=2)
        fila.grid_columnconfigure(1, weight=1)

        color = COLORES_PERFIL[indice % len(COLORES_PERFIL)]
        inicial = (perfil["nombre"][:1].upper() or "?")
        ctk.CTkLabel(fila, text=inicial, width=40, height=40, corner_radius=20,
                     fg_color=color, text_color="#0e131a",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=(12, 10), pady=10)

        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", pady=10)
        ctk.CTkLabel(col, text=perfil["nombre"], font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"{n_votos} voto{'s' if n_votos != 1 else ''} emitidos"
                               f" · nota media {media:.1f}",
                     font=ctk.CTkFont(size=12), text_color=COLOR_TEXTO_2,
                     anchor="w").pack(fill="x")

        ctk.CTkButton(fila, text="✏️  Renombrar", width=110, height=32, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda p=perfil: self._renombrar(p)).grid(
            row=0, column=2, padx=6, pady=8)
        ctk.CTkButton(fila, text="🗑", width=40, height=32, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#4a2323", hover_color="#6b3030",
                      command=lambda p=perfil: self._eliminar(p)).grid(
            row=0, column=3, padx=(0, 12), pady=8)

    def _renombrar(self, perfil: dict):
        def al_aceptar(nombre):
            self.app.renombrar_perfil(perfil["id"], nombre)
            self.refrescar()
        VentanaTexto(self, "Renombrar perfil", "Nuevo nombre:",
                     al_aceptar, valor_inicial=perfil["nombre"])

    def _eliminar(self, perfil: dict):
        if len(self.app.datos["perfiles"]) <= 1:
            messagebox.showwarning("Perfiles", "Necesitas al menos un perfil.")
            return
        if messagebox.askyesno("Eliminar perfil",
                               f"¿Eliminar '{perfil['nombre']}' y todos sus votos?"):
            self.app.eliminar_perfil(perfil["id"])
            self.refrescar()


class VentanaVoto(ctk.CTkToplevel):
    """Desglose detallado de UN voto (las puntuaciones de un perfil)."""

    def __init__(self, master, cata: dict, voto: dict, nombre_perfil: str):
        super().__init__(master)
        self.title(f"Voto de {nombre_perfil} — {cata.get('nombre', '')}")
        self.geometry("560x640")
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        ctk.CTkLabel(self, text=f"👤 {nombre_perfil}", font=ctk.CTkFont(size=20, weight="bold"),
                     anchor="w").pack(fill="x", padx=22, pady=(18, 0))
        ctk.CTkLabel(self, text=f"{cata.get('nombre', '')} · {voto.get('fecha', '')}",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2, anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 4))
        ctk.CTkLabel(self, text=f"Nota: {_flotante(voto.get('nota_final')):.1f} / 100",
                     font=ctk.CTkFont(size=18, weight="bold"), text_color=COLOR_DORADO,
                     anchor="w").pack(fill="x", padx=22, pady=(0, 12))

        contenedor = ctk.CTkScrollableFrame(self, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        detalle = voto.get("puntuaciones_detalle", {})
        notas = voto.get("notas_bloques", {})
        for meta in BLOQUES:
            marco = ctk.CTkFrame(contenedor, corner_radius=8, fg_color=COLOR_TARJETA,
                                 border_width=1, border_color=meta["color"])
            marco.pack(fill="x", pady=(0, 10))
            cab = ctk.CTkFrame(marco, fg_color="transparent")
            cab.pack(fill="x", padx=14, pady=(10, 2))
            ctk.CTkLabel(cab, text=f"{meta['titulo']}  ·  {int(meta['peso'] * 100)}%",
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=meta["texto_color"]).pack(side="left")
            ctk.CTkLabel(cab, text=f"{_flotante(notas.get(meta['clave'])):.1f}",
                         font=ctk.CTkFont(size=16, weight="bold"),
                         text_color=color_nota(_flotante(notas.get(meta['clave'])) / 10),
            ).pack(side="right")

            scores = detalle.get(meta["clave"], {})
            for clave, etiqueta in meta["subs"]:
                valor = _flotante(scores.get(clave))
                fila = ctk.CTkFrame(marco, fg_color="transparent")
                fila.pack(fill="x", padx=14, pady=2)
                ctk.CTkLabel(fila, text=etiqueta, font=ctk.CTkFont(size=12),
                             text_color="#aab3c2", width=200, anchor="w").pack(side="left")
                barra = ctk.CTkProgressBar(fila, width=140, height=10,
                                           progress_color=meta["color"], fg_color="#2A3240")
                barra.set(valor / 10.0)
                barra.pack(side="left", padx=8)
                ctk.CTkLabel(fila, text=f"{valor:.1f}", width=40,
                             font=ctk.CTkFont(size=12, weight="bold")).pack(side="right")

        ctk.CTkButton(self, text="Cerrar", width=120, height=36, corner_radius=8,
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self.destroy).pack(pady=(0, 16))


class VentanaDetalle(ctk.CTkToplevel):
    """
    Detalle de una cata: foto, nota media, chips y la lista de TODOS los votos
    (quién votó, cuándo y cuánto), con desglose individual y borrado de votos.
    """

    def __init__(self, master, cata: dict, al_eliminar_voto, al_eliminar_cata):
        super().__init__(master)
        self.cata = cata
        self._al_eliminar_voto = al_eliminar_voto
        self._al_eliminar_cata = al_eliminar_cata

        self.title(f"Detalle — {cata.get('nombre', 'Cata')}")
        self.geometry("640x800")
        self.minsize(560, 620)
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.after(10, self._centrar)

        # Foto
        ruta = resolver_ruta_foto(cata.get("foto"))
        img = imagen_ctk(ruta, 300, 220) if ruta else None
        if img is not None:
            ctk.CTkLabel(self, text="", image=img).pack(pady=(18, 4))

        ctk.CTkLabel(self, text=cata.get("nombre", "—"), font=ctk.CTkFont(size=24, weight="bold"),
                     anchor="w").pack(fill="x", padx=22, pady=(10, 0))

        votos = votos_validos(cata)
        media = nota_media(cata)
        ctk.CTkLabel(self, text=f"Nota media: {media:.1f} / 100  ·  {len(votos)} voto"
                                f"{'s' if len(votos) != 1 else ''}",
                     font=ctk.CTkFont(size=18, weight="bold"), text_color=COLOR_DORADO,
                     anchor="w").pack(fill="x", padx=22, pady=(0, 6))

        # Chips tipo/país
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(fill="x", padx=22, pady=(0, 8))
        tipo = cata.get("tipo", "—")
        ctk.CTkLabel(chips, text=tipo, corner_radius=12,
                     fg_color=COLOR_TIPO.get(tipo, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        pais = cata.get("pais", "")
        ctk.CTkLabel(chips, text=pais or "—", corner_radius=12,
                     fg_color=COLOR_PAIS.get(pais, "#444444"), text_color="#0e131a",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

        ctk.CTkLabel(self, anchor="w", text_color=COLOR_TEXTO_2, justify="left",
                     text=(f"🏭 {cata.get('productor', '—') or '—'}    "
                           f"📅 {cata.get('fecha', '—')}    🆔 {cata.get('id', '—')}"),
        ).pack(fill="x", padx=22, pady=(0, 10))

        # Votos por perfil
        contenedor = ctk.CTkScrollableFrame(self, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        ctk.CTkLabel(contenedor, text="VOTOS POR PERFIL", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXTO_2, anchor="w").pack(fill="x", pady=(0, 6))

        perfiles_id = {p["id"]: p["nombre"] for p in self._perfiles()}
        if votos:
            for v in votos:
                nombre = perfiles_id.get(v.get("perfil_id"), "¿Perfil eliminado?")
                self._fila_voto(contenedor, v, nombre)
        else:
            ctk.CTkLabel(contenedor, text="Esta cata todavía no tiene votos.",
                         font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2).pack(pady=8)

        # Comentarios
        if cata.get("comentarios"):
            ctk.CTkLabel(self, text="Comentarios", font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w").pack(fill="x", padx=22)
            ctk.CTkLabel(self, text=cata["comentarios"], wraplength=560, justify="left",
                         text_color="#aab3c2").pack(fill="x", padx=22, pady=(2, 10))

        # Acciones
        acciones = ctk.CTkFrame(self, fg_color="transparent")
        acciones.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkButton(acciones, text="Cerrar", width=120, height=38, corner_radius=8,
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(acciones, text="🗑  Eliminar cata", width=150, height=38, corner_radius=8,
                      fg_color=COLOR_PELIGRO, hover_color="#c24242",
                      command=self._eliminar_cata).pack(side="left")

    def _perfiles(self):
        return self.master.datos["perfiles"] if hasattr(self.master, "datos") else []

    def _fila_voto(self, parent, voto: dict, nombre_perfil: str):
        nota = _flotante(voto.get("nota_final"))
        fila = ctk.CTkFrame(parent, corner_radius=8, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.pack(fill="x", pady=3)
        fila.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(fila, text="👤", font=ctk.CTkFont(size=16)).grid(
            row=0, column=0, padx=(10, 6), pady=8)
        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", pady=6)
        ctk.CTkLabel(col, text=nombre_perfil, font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=voto.get("fecha", ""), font=ctk.CTkFont(size=11),
                     text_color=COLOR_TEXTO_2, anchor="w").pack(fill="x")

        ctk.CTkLabel(fila, text=f"{nota:.1f}", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=color_nota(nota / 10), width=70, anchor="e").grid(
            row=0, column=2, padx=6, pady=8)
        ctk.CTkButton(fila, text="Desglose", width=86, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#232B36", hover_color="#2E3846",
                      command=lambda v=voto, n=nombre_perfil: VentanaVoto(
                          self, self.cata, v, n)).grid(row=0, column=3, padx=4, pady=8)
        ctk.CTkButton(fila, text="🗑", width=36, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=12), fg_color="#4a2323", hover_color="#6b3030",
                      command=lambda v=voto: self._eliminar_voto(v)).grid(
            row=0, column=4, padx=(0, 10), pady=8)

    def _eliminar_voto(self, voto: dict):
        nombre = next((p["nombre"] for p in self._perfiles()
                       if p["id"] == voto.get("perfil_id")), "este perfil")
        if messagebox.askyesno("Eliminar voto", f"¿Eliminar el voto de {nombre}?"):
            self._al_eliminar_voto(voto.get("perfil_id"))
            self.destroy()

    def _eliminar_cata(self):
        if messagebox.askyesno("Confirmar eliminación",
                               f"¿Eliminar '{self.cata.get('nombre', '')}' y TODOS sus votos?"):
            self._al_eliminar_cata()
            self.destroy()

    def _centrar(self):
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        x = padre.winfo_x() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_y() + (padre.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class VentanaProductor(ctk.CTkToplevel):
    """
    Detalle de un productor: ver sus catas, renombrarlo y añadirle foto.
    """

    def __init__(self, master, nombre_productor: str, al_renombrar, al_foto,
                 al_ver_cata):
        super().__init__(master)
        self._nombre = nombre_productor
        self._al_renombrar = al_renombrar
        self._al_foto = al_foto
        self._al_ver_cata = al_ver_cata

        self.title(f"Productor — {nombre_productor}")
        self.geometry("640x640")
        self.minsize(560, 520)
        self.configure(fg_color=COLOR_FONDO)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.after(10, self._centrar)

        self._construir()

    def _construir(self):
        for w in self.winfo_children():
            w.destroy()

        catas = [c for c in self._todas_catas() if str(c.get("productor", "")).strip() == self._nombre]
        medias = [nota_media(c) for c in catas]
        media = round(sum(medias) / len(medias), 1) if medias else 0.0

        # Cabecera
        cab = ctk.CTkFrame(self, fg_color="transparent")
        cab.pack(fill="x", padx=22, pady=(16, 0))

        ruta = resolver_ruta_foto(self._foto_productor())
        img = imagen_ctk(ruta, 84, 84) if ruta else None
        if img is not None:
            ctk.CTkLabel(cab, text="", image=img, width=84, height=84).pack(side="left",
                                                                            padx=(0, 14))
        else:
            ctk.CTkLabel(cab, text="🏭", width=84, height=84, corner_radius=10,
                         fg_color="#101318", font=ctk.CTkFont(size=30)).pack(
                side="left", padx=(0, 14))

        col = ctk.CTkFrame(cab, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text=self._nombre, font=ctk.CTkFont(size=22, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"{len(catas)} cata{'s' if len(catas) != 1 else ''} · "
                               f"nota media {media:.1f}",
                     font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2,
                     anchor="w").pack(fill="x")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(10, 8))
        ctk.CTkButton(btns, text="✏️  Renombrar", width=120, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#232B36", hover_color="#2E3846",
                      command=self._renombrar).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btns, text="📷  Añadir foto", width=130, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                      command=self._foto).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btns, text="🗑  Eliminar", width=110, height=34, corner_radius=8,
                      font=ctk.CTkFont(size=13), fg_color="#4a2323", hover_color="#6b3030",
                      command=self._eliminar).pack(side="left")

        ctk.CTkLabel(self, text="CATAS DE ESTE PRODUCTOR",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR_TEXTO_2,
                     anchor="w").pack(fill="x", padx=22, pady=(4, 6))

        cuerpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        if not catas:
            ctk.CTkLabel(cuerpo, text="Este productor no tiene catas registradas.",
                         font=ctk.CTkFont(size=13), text_color=COLOR_TEXTO_2).pack(pady=20)
        else:
            catas.sort(key=lambda c: -nota_media(c))
            for c in catas:
                self._fila_cata(cuerpo, c)

        ctk.CTkButton(self, text="Cerrar", width=120, height=36, corner_radius=8,
                      fg_color="#232B36", hover_color="#2E3846",
                      command=self.destroy).pack(pady=(0, 14))

    def _todas_catas(self):
        return self.master.datos["catas"] if hasattr(self.master, "datos") else []

    def _foto_productor(self):
        ent = self.master.buscar_productor(self._nombre) if hasattr(self.master, "buscar_productor") else None
        return (ent or {}).get("foto", "")

    def _fila_cata(self, parent, cata: dict):
        media = nota_media(cata)
        fila = ctk.CTkFrame(parent, corner_radius=8, fg_color=COLOR_TARJETA,
                            border_width=1, border_color=COLOR_BORDE)
        fila.pack(fill="x", pady=3)
        fila.grid_columnconfigure(1, weight=1)
        fila.bind("<Double-Button-1>", lambda _e, c=cata: self._al_ver_cata(c))

        ruta = resolver_ruta_foto(cata.get("foto"))
        img = imagen_ctk(ruta, 36, 36) if ruta else None
        if img is not None:
            ctk.CTkLabel(fila, text="", image=img, width=36, height=36).grid(
                row=0, column=0, padx=(10, 8), pady=6)
        else:
            ctk.CTkLabel(fila, text="🌿", width=36, height=36, corner_radius=6,
                         fg_color="#101318", font=ctk.CTkFont(size=14)).grid(
                row=0, column=0, padx=(10, 8), pady=6)

        col = ctk.CTkFrame(fila, fg_color="transparent")
        col.grid(row=0, column=1, sticky="ew", pady=6)
        ctk.CTkLabel(col, text=cata.get("nombre", "—"), font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=f"{cata.get('tipo', '—')} · {cata.get('pais', '—')} · "
                               f"{len(votos_validos(cata))} voto"
                               f"{'s' if len(votos_validos(cata)) != 1 else ''}",
                     font=ctk.CTkFont(size=11), text_color=COLOR_TEXTO_2,
                     anchor="w").pack(fill="x")

        ctk.CTkLabel(fila, text=f"{media:.1f}", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COLOR_DORADO, width=70, anchor="e").grid(
            row=0, column=2, padx=6, pady=8)
        ctk.CTkButton(fila, text="Ver", width=48, height=28, font=ctk.CTkFont(size=12),
                      fg_color="#232B36", hover_color="#2E3846",
                      command=lambda c=cata: self._al_ver_cata(c)).grid(
            row=0, column=3, padx=(0, 10), pady=6)

    def _renombrar(self):
        def al_aceptar(nuevo):
            self._al_renombrar(self._nombre, nuevo)
            self._nombre = nuevo
            self.title(f"Productor — {nuevo}")
            self._construir()
        VentanaTexto(self, "Renombrar productor", "Nuevo nombre del productor:",
                     al_aceptar, valor_inicial=self._nombre)

    def _foto(self):
        ruta = filedialog.askopenfilename(
            title="Foto del productor",
            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Todos los archivos", "*.*")])
        if ruta:
            self._al_foto(self._nombre, ruta)
            self._construir()

    def _eliminar(self):
        if messagebox.askyesno("Eliminar productor",
                               f"¿Eliminar '{self._nombre}' del catálogo?"):
            if self.master.eliminar_productor(self._nombre):
                self.destroy()

    def _centrar(self):
        self.update_idletasks()
        padre = self.master.winfo_toplevel()
        x = padre.winfo_x() + (padre.winfo_width() - self.winfo_width()) // 2
        y = padre.winfo_y() + (padre.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

# =============================================================================
# 4. CAPA DE ORQUESTACIÓN — AppCatas
# =============================================================================

class AppCatas(ctk.CTk):
    """Ventana principal: sidebar + 4 vistas + mediador de datos."""

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title("Sistema de Catas — Registro y Rankings")
        self.geometry("1200x820")
        self.minsize(1000, 680)

        self.datos = cargar_datos()  # estructura v2: perfiles + fotos + catas

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---------------- Sidebar ----------------
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLOR_SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(7, weight=1)  # Espaciador entre botones y pie
        sidebar.grid_propagate(False)

        logo = ctk.CTkFrame(sidebar, fg_color="#14231A", corner_radius=12,
                            border_width=1, border_color=COLOR_DORADO)
        logo.grid(row=0, column=0, padx=16, pady=(24, 18))
        ctk.CTkLabel(logo, text="🌿  CATAS", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COLOR_VERDE_L).pack(padx=16, pady=(6, 0))
        ctk.CTkLabel(logo, text="multi-voto · v2.0", font=ctk.CTkFont(size=10),
                     text_color=COLOR_DORADO).pack(pady=(0, 4))

        ctk.CTkFrame(sidebar, height=2, fg_color="#1E242E").grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        self.btn_nueva = ctk.CTkButton(sidebar, text="➕  Nueva Cata",
                                       font=ctk.CTkFont(size=14, weight="bold"),
                                       fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO,
                                       height=42, corner_radius=8,
                                       command=lambda: self._mostrar_vista("nueva"))
        self.btn_nueva.grid(row=2, column=0, padx=16, pady=5, sticky="ew")

        self.btn_productos = ctk.CTkButton(sidebar, text="📦  Productos",
                                           font=ctk.CTkFont(size=14),
                                           fg_color="transparent", border_width=1,
                                           border_color="#2A3240", hover_color="#1B212B",
                                           height=42, corner_radius=8,
                                           command=lambda: self._mostrar_vista("productos"))
        self.btn_productos.grid(row=3, column=0, padx=16, pady=5, sticky="ew")

        self.btn_productores = ctk.CTkButton(sidebar, text="🏭  Productores",
                                             font=ctk.CTkFont(size=14),
                                             fg_color="transparent", border_width=1,
                                             border_color="#2A3240", hover_color="#1B212B",
                                             height=42, corner_radius=8,
                                             command=lambda: self._mostrar_vista("productores"))
        self.btn_productores.grid(row=4, column=0, padx=16, pady=5, sticky="ew")

        self.btn_rankings = ctk.CTkButton(sidebar, text="🏆  Rankings",
                                          font=ctk.CTkFont(size=14),
                                          fg_color="transparent", border_width=1,
                                          border_color="#2A3240", hover_color="#1B212B",
                                          height=42, corner_radius=8,
                                          command=lambda: self._mostrar_vista("rankings"))
        self.btn_rankings.grid(row=5, column=0, padx=16, pady=5, sticky="ew")

        self.btn_perfiles = ctk.CTkButton(sidebar, text="👥  Perfiles",
                                          font=ctk.CTkFont(size=14),
                                          fg_color="transparent", border_width=1,
                                          border_color="#2A3240", hover_color="#1B212B",
                                          height=42, corner_radius=8,
                                          command=lambda: self._mostrar_vista("perfiles"))
        self.btn_perfiles.grid(row=6, column=0, padx=16, pady=5, sticky="ew")

        self.lbl_total = ctk.CTkLabel(sidebar, text="", font=ctk.CTkFont(size=12),
                                      text_color=COLOR_TEXTO_2, fg_color="#181D25",
                                      corner_radius=10)
        self.lbl_total.grid(row=8, column=0, pady=(0, 10), padx=16, sticky="ew")
        self._actualizar_contador()

        # ---------------- Contenedor de vistas ----------------
        self.contenedor = ctk.CTkFrame(self, fg_color=COLOR_FONDO, corner_radius=0)
        self.contenedor.grid(row=0, column=1, sticky="nsew")
        self.contenedor.grid_columnconfigure(0, weight=1)
        self.contenedor.grid_rowconfigure(0, weight=1)

        self.vista_formulario = VistaFormulario(self.contenedor, app=self)
        self.vista_productos = VistaProductos(self.contenedor, app=self)
        self.vista_productores = VistaProductores(self.contenedor, app=self)
        self.vista_rankings = VistaRankings(self.contenedor, app=self)
        self.vista_perfiles = VistaPerfiles(self.contenedor, app=self)

        self._mostrar_vista("nueva")

    # ------------------------------------------------------------ Mediador

    def notificar_cambio(self):
        """Única vía de refresco tras cualquier modificación de datos."""
        self._actualizar_contador()
        self.vista_productos.refrescar()
        self.vista_productores.refrescar()
        self.vista_rankings.refrescar()
        self.vista_perfiles.refrescar()

    def _actualizar_contador(self):
        total = len(self.datos["catas"])
        votos = sum(len(votos_validos(c)) for c in self.datos["catas"])
        self.lbl_total.configure(
            text=(f"🌿 {total} cata{'s' if total != 1 else ''} · {votos} voto"
                  f"{'s' if votos != 1 else ''}" if total else "Sin catas todavía"))

    def _mostrar_vista(self, nombre: str):
        vistas = {"nueva": self.vista_formulario, "productos": self.vista_productos,
                  "productores": self.vista_productores,
                  "rankings": self.vista_rankings, "perfiles": self.vista_perfiles}
        botones = {"nueva": self.btn_nueva, "productos": self.btn_productos,
                   "productores": self.btn_productores,
                   "rankings": self.btn_rankings, "perfiles": self.btn_perfiles}
        for v in vistas.values():
            v.grid_forget()
        for b in botones.values():
            b.configure(fg_color="transparent", hover_color="#1B212B")

        if nombre == "nueva":
            self.vista_formulario.grid(row=0, column=0, sticky="nsew", padx=26, pady=16)
        elif nombre == "productos":
            self.datos = cargar_datos()
            self._actualizar_contador()
            self.vista_productos.refrescar()
            self.vista_productos.grid(row=0, column=0, sticky="nsew", padx=26, pady=16)
        elif nombre == "productores":
            self.datos = cargar_datos()
            self._actualizar_contador()
            self.vista_productores.refrescar()
            self.vista_productores.grid(row=0, column=0, sticky="nsew", padx=26, pady=16)
        elif nombre == "rankings":
            self.datos = cargar_datos()
            self._actualizar_contador()
            self.vista_rankings.refrescar()
            self.vista_rankings.grid(row=0, column=0, sticky="nsew", padx=26, pady=16)
        else:
            self.vista_perfiles.refrescar()
            self.vista_perfiles.grid(row=0, column=0, sticky="nsew", padx=26, pady=16)
        botones[nombre].configure(fg_color=COLOR_VERDE, hover_color=COLOR_ACTIVO)

    # ------------------------------------------------------------ Perfiles

    def crear_perfil(self, nombre: str):
        """Crea un perfil con nombre único (case-insensitive)."""
        nombre = nombre.strip()
        if not nombre:
            return
        existentes = {p["nombre"].lower() for p in self.datos["perfiles"]}
        if nombre.lower() in existentes:
            messagebox.showinfo("Perfil", f"Ya existe un perfil llamado '{nombre}'.")
            return
        self.datos["perfiles"].append({
            "id": generar_id({p["id"] for p in self.datos["perfiles"]}, prefijo="p_"),
            "nombre": nombre,
        })
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.vista_formulario.refrescar_perfiles()
        self.vista_rankings.refrescar_perfiles()

    def renombrar_perfil(self, perfil_id: str, nuevo: str):
        nuevo = nuevo.strip()
        if not nuevo:
            return
        for p in self.datos["perfiles"]:
            if p["id"] == perfil_id:
                p["nombre"] = nuevo
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.vista_formulario.refrescar_perfiles()
        self.vista_rankings.refrescar_perfiles()

    def eliminar_perfil(self, perfil_id: str):
        """Elimina el perfil y TODOS sus votos de todas las catas."""
        self.datos["perfiles"] = [p for p in self.datos["perfiles"] if p["id"] != perfil_id]
        for c in self.datos["catas"]:
            quitar_voto(c, perfil_id)
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.vista_formulario.refrescar_perfiles()
        self.vista_rankings.refrescar_perfiles()

    # ------------------------------------------------------------ Productores

    def nombres_productores(self) -> list:
        return [p["nombre"] for p in self.datos["productores"]]

    def buscar_productor(self, nombre: str):
        for p in self.datos["productores"]:
            if p["nombre"] == nombre:
                return p
        return None

    def crear_productor(self, nombre: str) -> bool:
        """Crea un productor con nombre único; True si se creó."""
        nombre = nombre.strip()
        if not nombre:
            return False
        if any(p["nombre"].lower() == nombre.lower() for p in self.datos["productores"]):
            messagebox.showinfo("Productor", f"Ya existe '{nombre}'.")
            return False
        self.datos["productores"].append({
            "id": generar_id({p.get("id", "") for p in self.datos["productores"]},
                             prefijo="pr_"),
            "nombre": nombre,
            "foto": "",
        })
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.refrescar_productores()
        return True

    def renombrar_productor(self, viejo: str, nuevo: str):
        """Renombra la entidad y todas sus catas (conserva la foto)."""
        nuevo = nuevo.strip()
        if not nuevo or nuevo == viejo:
            return
        ent = self.buscar_productor(viejo)
        if ent is not None:
            if any(p["nombre"].lower() == nuevo.lower() and p is not ent
                   for p in self.datos["productores"]):
                messagebox.showwarning("Productor", f"Ya existe '{nuevo}'.")
                return
            ent["nombre"] = nuevo
        for c in self.datos["catas"]:
            if str(c.get("productor", "")).strip() == viejo:
                c["productor"] = nuevo
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.refrescar_productores()

    def eliminar_productor(self, nombre: str) -> bool:
        """Elimina la entidad si no tiene catas asignadas; False si está en uso."""
        n_catas = sum(1 for c in self.datos["catas"]
                      if str(c.get("productor", "")).strip() == nombre)
        if n_catas:
            messagebox.showwarning(
                "Productor en uso",
                f"'{nombre}' tiene {n_catas} cata{'s' if n_catas != 1 else ''}. "
                "Bórralas o reasígnalas antes de eliminar el productor.")
            return False
        self.datos["productores"] = [p for p in self.datos["productores"]
                                     if p["nombre"] != nombre]
        guardar_datos(self.datos)
        self.notificar_cambio()
        self.refrescar_productores()
        return True

    def actualizar_foto_productor(self, nombre: str, ruta: str):
        """Copia la foto del productor a ./imagenes/ y la guarda en su entidad."""
        ext = os.path.splitext(ruta)[1].lower() or ".jpg"
        rid = generar_id({p.get("foto", "") for p in self.datos["productores"]},
                         prefijo="pr_")
        os.makedirs(RUTA_IMAGENES, exist_ok=True)
        destino = os.path.join(RUTA_IMAGENES, f"{rid}{ext}")
        try:
            shutil.copy2(ruta, destino)
        except OSError:
            messagebox.showerror("Foto", "No se pudo copiar la imagen.")
            return
        ent = self.buscar_productor(nombre)
        if ent is not None:
            ent["foto"] = f"imagenes/{rid}{ext}"
        guardar_datos(self.datos)
        self.notificar_cambio()

    def refrescar_productores(self):
        """Actualiza el selector de productores del formulario."""
        self.vista_formulario.refrescar_productores()

    # ------------------------------------------------------------ Detalles

    def abrir_detalle(self, cata: dict):
        """Detalle de cata: lista de votos, desglose y borrado."""
        def al_eliminar_voto(perfil_id):
            quitar_voto(cata, perfil_id)
            guardar_datos(self.datos)
            self.notificar_cambio()

        def al_eliminar_cata():
            self.datos["catas"] = [c for c in self.datos["catas"] if c.get("id") != cata.get("id")]
            guardar_datos(self.datos)
            self.notificar_cambio()

        VentanaDetalle(self, cata, al_eliminar_voto, al_eliminar_cata)

    def abrir_producto(self, cata: dict):
        """Ficha completa del producto (especificaciones + votaciones editables)."""
        VentanaProducto(self, cata)

    def abrir_productor(self, nombre: str):
        VentanaProductor(self, nombre,
                         al_renombrar=self.renombrar_productor,
                         al_foto=self.actualizar_foto_productor,
                         al_ver_cata=self.abrir_detalle)


def main():
    app = AppCatas()
    app.mainloop()


if __name__ == "__main__":
    main()

