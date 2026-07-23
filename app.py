"""Interface grafica do GPX Analisador de Rotas (customtkinter).

Pensada para usuarios nao tecnicos. O processamento roda numa THREAD separada,
comunicando-se com a interface por uma fila (queue) - a interface nunca trava.
O botao Cancelar sinaliza um Event; o nucleo interrompe entre etapas seguras,
sem corromper arquivos nem o banco.

Execucao:  python app.py
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk  # noqa: E402

from src import config_loader, gpx_reader, utils  # noqa: E402
from src.database import Database  # noqa: E402
from src.models import CONF_NAO_IDENT, FileResult  # noqa: E402
from src.route_processor import BatchProcessor, Reporter  # noqa: E402

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("GPX Analisador de Rotas - CATTER Engenharia")
        self.geometry("1120x780")
        self.minsize(980, 680)

        self.cfg = config_loader.carregar_config()
        self.fila: "queue.Queue" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.arquivos: list[Path] = []
        self.saida_dir = tk.StringVar(value=str(utils.default_output_dir()))

        self._vars_config()
        self._montar()
        self.after(120, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    # ------------------------------------------------------------------ vars
    def _vars_config(self):
        g = self.cfg.get
        self.v_recursivo = tk.BooleanVar(value=True)
        self.v_excel = tk.BooleanVar(value=g("saidas.gerar_excel", True))
        self.v_mapa = tk.BooleanVar(value=g("saidas.gerar_mapa_html", True))
        self.v_gpkg = tk.BooleanVar(value=g("saidas.gerar_geopackage", False))
        self.v_json = tk.BooleanVar(value=g("saidas.gerar_json", True))
        self.v_diag = tk.BooleanVar(value=g("gerar_diagnostico_pontos", False))
        self.v_abrir = tk.BooleanVar(value=g("saidas.abrir_pasta_ao_terminar", False))
        self.v_offline = tk.BooleanVar(value=g("modo_offline", False))
        self.v_atualizar = tk.BooleanVar(value=g("atualizar_cache", False))

        self.v_buffer = tk.StringVar(value=str(g("buffer_ruas_m")))
        self.v_espac = tk.StringVar(value=str(g("espacamento_pontos_m")))
        self.v_raio = tk.StringVar(value=str(g("raio_candidatos_m")))
        self.v_valid = tk.StringVar(value=str(g("cache_validade_dias")))
        # avancado
        self.v_dp = tk.StringVar(value=str(g("tolerancia_douglas_peucker_m")))
        self.v_ang = tk.StringVar(value=str(g("angulo_preservacao_curva_graus")))
        self.v_distmax = tk.StringVar(value=str(g("distancia_maxima_aceitavel_m")))
        self.v_perm = tk.StringVar(value=str(g("permanencia_minima_trecho_m")))
        self.v_salto = tk.StringVar(value=str(g("salto_velocidade_maxima_kmh")))
        self.v_tile = tk.StringVar(value=str(g("tile_tamanho_km")))

    # ------------------------------------------------------------------ UI
    def _montar(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        esq = ctk.CTkScrollableFrame(self, label_text="Configuracao")
        esq.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        dir_ = ctk.CTkFrame(self)
        dir_.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        dir_.grid_rowconfigure(3, weight=1)
        dir_.grid_columnconfigure(0, weight=1)

        self._sec_entrada(esq)
        self._sec_saida(esq)
        self._sec_basico(esq)
        self._sec_avancado(esq)
        self._sec_cache(esq)
        self._painel_exec(dir_)

    def _titulo(self, parent, texto):
        ctk.CTkLabel(parent, text=texto, font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=6, pady=(12, 2))

    def _sec_entrada(self, p):
        self._titulo(p, "1. Selecao de arquivos GPX")
        b = ctk.CTkFrame(p, fg_color="transparent")
        b.pack(fill="x", padx=6)
        ctk.CTkButton(b, text="Selecionar arquivo(s)", command=self._sel_arquivos).pack(side="left", padx=2)
        ctk.CTkButton(b, text="Selecionar pasta", command=self._sel_pasta).pack(side="left", padx=2)
        ctk.CTkCheckBox(b, text="Subpastas", variable=self.v_recursivo).pack(side="left", padx=8)

        lf = ctk.CTkFrame(p)
        lf.pack(fill="x", padx=6, pady=4)
        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.lista = tk.Listbox(lf, selectmode=tk.EXTENDED, height=7,
                                yscrollcommand=sb.set, activestyle="none")
        self.lista.pack(side="left", fill="both", expand=True)
        sb.config(command=self.lista.yview)

        b2 = ctk.CTkFrame(p, fg_color="transparent")
        b2.pack(fill="x", padx=6)
        self.lbl_qtd = ctk.CTkLabel(b2, text="0 arquivo(s)")
        self.lbl_qtd.pack(side="left", padx=2)
        ctk.CTkButton(b2, text="Remover selecionados", width=160,
                      command=self._remover).pack(side="right", padx=2)
        ctk.CTkButton(b2, text="Limpar lista", width=110,
                      command=self._limpar_lista).pack(side="right", padx=2)

    def _sec_saida(self, p):
        self._titulo(p, "2. Saida")
        f = ctk.CTkFrame(p, fg_color="transparent")
        f.pack(fill="x", padx=6)
        ctk.CTkButton(f, text="Pasta de saida", width=120,
                      command=self._sel_saida).pack(side="left", padx=2)
        ctk.CTkEntry(f, textvariable=self.saida_dir).pack(side="left", fill="x", expand=True, padx=2)
        g = ctk.CTkFrame(p, fg_color="transparent")
        g.pack(fill="x", padx=6, pady=2)
        opcoes = (("Excel", self.v_excel), ("Mapa HTML", self.v_mapa),
                  ("GeoPackage", self.v_gpkg), ("JSON", self.v_json),
                  ("Diagnostico", self.v_diag), ("Abrir pasta ao fim", self.v_abrir))
        for i, (txt, var) in enumerate(opcoes):
            ctk.CTkCheckBox(g, text=txt, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=6, pady=2)

    def _campo(self, parent, rotulo, var, largura=70):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=6, pady=1)
        ctk.CTkLabel(f, text=rotulo, anchor="w", width=260).pack(side="left")
        ctk.CTkEntry(f, textvariable=var, width=largura).pack(side="right")

    def _sec_basico(self, p):
        self._titulo(p, "3. Configuracoes basicas")
        self._campo(p, "Margem p/ baixar ruas (m)", self.v_buffer)
        self._campo(p, "Espacamento da simplificacao (m)", self.v_espac)
        self._campo(p, "Raio maximo p/ encontrar vias (m)", self.v_raio)
        self._campo(p, "Validade do cache (dias)", self.v_valid)
        f = ctk.CTkFrame(p, fg_color="transparent")
        f.pack(fill="x", padx=6, pady=2)
        ctk.CTkCheckBox(f, text="Somente cache (offline)", variable=self.v_offline).pack(side="left", padx=6)
        ctk.CTkCheckBox(f, text="Atualizar ruas ja baixadas", variable=self.v_atualizar).pack(side="left", padx=6)

    def _sec_avancado(self, p):
        self._titulo(p, "4. Avancado")
        self.frame_av = ctk.CTkFrame(p)
        self._av_visivel = tk.BooleanVar(value=False)
        ctk.CTkButton(p, text="Mostrar/ocultar avancado",
                      command=self._toggle_av).pack(fill="x", padx=6, pady=2)
        self._campo(self.frame_av, "Tolerancia Douglas-Peucker (m)", self.v_dp)
        self._campo(self.frame_av, "Angulo p/ preservar curva (graus)", self.v_ang)
        self._campo(self.frame_av, "Distancia maxima aceitavel (m)", self.v_distmax)
        self._campo(self.frame_av, "Permanencia minima no trecho (m)", self.v_perm)
        self._campo(self.frame_av, "Salto de velocidade maximo (km/h)", self.v_salto)
        self._campo(self.frame_av, "Tamanho do tile (km)", self.v_tile)

    def _toggle_av(self):
        if self._av_visivel.get():
            self.frame_av.pack_forget()
            self._av_visivel.set(False)
        else:
            self.frame_av.pack(fill="x", padx=6, pady=2)
            self._av_visivel.set(True)

    def _sec_cache(self, p):
        self._titulo(p, "5. Cache de vias (OSM)")
        f = ctk.CTkFrame(p, fg_color="transparent")
        f.pack(fill="x", padx=6, pady=2)
        ctk.CTkButton(f, text="Ver tamanho do cache", command=self._ver_cache).pack(side="left", padx=2)
        ctk.CTkButton(f, text="Limpar cache", fg_color="#B04A3A",
                      command=self._limpar_cache).pack(side="left", padx=2)

    def _painel_exec(self, d):
        ctk.CTkLabel(d, text="Execucao", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        bf = ctk.CTkFrame(d, fg_color="transparent")
        bf.grid(row=1, column=0, sticky="ew", padx=10)
        self.btn_proc = ctk.CTkButton(bf, text="Processar", command=self._processar)
        self.btn_proc.pack(side="left", padx=2)
        self.btn_canc = ctk.CTkButton(bf, text="Cancelar", state="disabled",
                                      fg_color="#B04A3A", command=self._cancelar)
        self.btn_canc.pack(side="left", padx=2)
        ctk.CTkButton(bf, text="Resetar", command=self._resetar).pack(side="left", padx=2)

        pf = ctk.CTkFrame(d)
        pf.grid(row=2, column=0, sticky="ew", padx=10, pady=6)
        pf.grid_columnconfigure(0, weight=1)
        self.lbl_arq = ctk.CTkLabel(pf, text="Aguardando...", anchor="w")
        self.lbl_arq.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        self.lbl_etapa = ctk.CTkLabel(pf, text="", anchor="w", text_color="#888")
        self.lbl_etapa.grid(row=1, column=0, sticky="ew", padx=8)
        ctk.CTkLabel(pf, text="Progresso geral", anchor="w").grid(row=2, column=0, sticky="w", padx=8)
        self.pb_geral = ctk.CTkProgressBar(pf)
        self.pb_geral.set(0)
        self.pb_geral.grid(row=3, column=0, sticky="ew", padx=8)
        ctk.CTkLabel(pf, text="Arquivo atual", anchor="w").grid(row=4, column=0, sticky="w", padx=8)
        self.pb_arq = ctk.CTkProgressBar(pf)
        self.pb_arq.set(0)
        self.pb_arq.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.lbl_cont = ctk.CTkLabel(pf, text="Processados: 0 | Erros: 0 | Conferir: 0", anchor="w")
        self.lbl_cont.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 6))

        ctk.CTkLabel(d, text="Log", anchor="w").grid(row=3, column=0, sticky="nw", padx=10)
        self.log = ctk.CTkTextbox(d, wrap="word")
        self.log.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        d.grid_rowconfigure(4, weight=1)

        self._n_proc = 0
        self._n_erro = 0
        self._n_conf = 0

    # ------------------------------------------------------------ acoes lista
    def _sel_arquivos(self):
        fs = filedialog.askopenfilenames(title="Selecionar GPX",
                                         filetypes=[("GPX", "*.gpx"), ("Todos", "*.*")])
        self._adicionar([Path(f) for f in fs])

    def _sel_pasta(self):
        d = filedialog.askdirectory(title="Selecionar pasta")
        if d:
            achados = gpx_reader.encontrar_gpx(d, recursivo=self.v_recursivo.get())
            self._adicionar(achados)

    def _adicionar(self, novos: list[Path]):
        atuais = set(self.arquivos)
        for n in novos:
            if n not in atuais:
                self.arquivos.append(n)
                self.lista.insert(tk.END, str(n))
        self._atualizar_qtd()

    def _remover(self):
        for i in reversed(self.lista.curselection()):
            self.lista.delete(i)
            del self.arquivos[i]
        self._atualizar_qtd()

    def _limpar_lista(self):
        self.lista.delete(0, tk.END)
        self.arquivos.clear()
        self._atualizar_qtd()

    def _atualizar_qtd(self):
        self.lbl_qtd.configure(text=f"{len(self.arquivos)} arquivo(s)")

    def _sel_saida(self):
        d = filedialog.askdirectory(title="Pasta de saida")
        if d:
            self.saida_dir.set(d)

    # ------------------------------------------------------------ cache
    def _ver_cache(self):
        try:
            db = Database()
            n = len(db.listar_tiles())
            tam = db.tamanho_cache_bytes()
            db.fechar()
            messagebox.showinfo("Cache", f"Tiles em cache: {n}\n"
                                         f"Tamanho aproximado: {tam/1_048_576:.1f} MB")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Cache", f"Erro: {e}")

    def _limpar_cache(self):
        if not messagebox.askyesno("Limpar cache",
                                   "Remover o catalogo de tiles e os arquivos de vias baixados?"):
            return
        try:
            db = Database()
            n = db.limpar_tiles()
            db.fechar()
            for f in utils.cache_dir().glob("*.graphml"):
                f.unlink(missing_ok=True)
            for f in utils.cache_dir().glob("*.gpkg"):
                f.unlink(missing_ok=True)
            messagebox.showinfo("Cache", f"{n} tile(s) removido(s) do catalogo.")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Cache", f"Erro: {e}")

    # ------------------------------------------------------------ execucao
    def _coletar_config(self) -> bool:
        try:
            self.cfg.set("buffer_ruas_m", float(self.v_buffer.get()))
            self.cfg.set("espacamento_pontos_m", float(self.v_espac.get()))
            self.cfg.set("raio_candidatos_m", float(self.v_raio.get()))
            self.cfg.set("cache_validade_dias", int(float(self.v_valid.get())))
            self.cfg.set("tolerancia_douglas_peucker_m", float(self.v_dp.get()))
            self.cfg.set("angulo_preservacao_curva_graus", float(self.v_ang.get()))
            self.cfg.set("distancia_maxima_aceitavel_m", float(self.v_distmax.get()))
            self.cfg.set("permanencia_minima_trecho_m", float(self.v_perm.get()))
            self.cfg.set("salto_velocidade_maxima_kmh", float(self.v_salto.get()))
            self.cfg.set("tile_tamanho_km", float(self.v_tile.get()))
        except ValueError:
            messagebox.showerror("Configuracao", "Ha valores numericos invalidos nas configuracoes.")
            return False
        self.cfg.set("modo_offline", self.v_offline.get())
        self.cfg.set("atualizar_cache", self.v_atualizar.get())
        self.cfg.set("saidas.gerar_excel", self.v_excel.get())
        self.cfg.set("saidas.gerar_mapa_html", self.v_mapa.get())
        self.cfg.set("saidas.gerar_geopackage", self.v_gpkg.get())
        self.cfg.set("saidas.gerar_json", self.v_json.get())
        self.cfg.set("gerar_diagnostico_pontos", self.v_diag.get())
        self.cfg.validar()
        return True

    def _processar(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.arquivos:
            messagebox.showwarning("Processar", "Selecione ao menos um arquivo GPX.")
            return
        if not self._coletar_config():
            return
        self.cancel_event.clear()
        self._n_proc = self._n_erro = self._n_conf = 0
        self.pb_geral.set(0)
        self.pb_arq.set(0)
        self.log.delete("1.0", tk.END)
        self.btn_proc.configure(state="disabled")
        self.btn_canc.configure(state="normal")

        arquivos = list(self.arquivos)
        saida = self.saida_dir.get()
        self.worker = threading.Thread(target=self._rodar, args=(arquivos, saida), daemon=True)
        self.worker.start()

    def _rodar(self, arquivos, saida):
        """Executado na THREAD de trabalho. So se comunica pela fila."""
        rep = Reporter(
            log=lambda m: self.fila.put(("log", m)),
            etapa=lambda e: self.fila.put(("etapa", e)),
            prog_geral=lambda a, t: self.fila.put(("pg", a, t)),
            prog_arquivo=lambda f: self.fila.put(("pa", f)),
            cancelado=self.cancel_event.is_set,
            arquivo_fim=lambda fr: self.fila.put(("fim_arq", fr)),
        )
        db = None
        try:
            utils.ensure_dirs()
            db = Database()
            bp = BatchProcessor(self.cfg, db, rep)
            resultados = bp.processar(arquivos, saida)
            self.fila.put(("fim", resultados, saida))
        except Exception as e:  # noqa: BLE001
            self.fila.put(("log", f"ERRO FATAL: {e}"))
            self.fila.put(("fim", [], saida))
        finally:
            if db:
                db.fechar()

    def _cancelar(self):
        self.cancel_event.set()
        self._log("Cancelamento solicitado; aguardando etapa segura...")
        self.btn_canc.configure(state="disabled")

    def _resetar(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Resetar", "Aguarde o processamento terminar ou cancele antes.")
            return
        self._limpar_lista()
        self.pb_geral.set(0)
        self.pb_arq.set(0)
        self.log.delete("1.0", tk.END)
        self.lbl_arq.configure(text="Aguardando...")
        self.lbl_etapa.configure(text="")
        self._n_proc = self._n_erro = self._n_conf = 0
        self._atualizar_contadores()

    # ------------------------------------------------------------ fila/UI
    def _poll(self):
        try:
            while True:
                item = self.fila.get_nowait()
                self._tratar(item)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _tratar(self, item):
        tipo = item[0]
        if tipo == "log":
            self._log(item[1])
        elif tipo == "etapa":
            self.lbl_etapa.configure(text=item[1])
        elif tipo == "pg":
            _, a, t = item
            self.pb_geral.set(a / t if t else 0)
            self.lbl_arq.configure(text=f"Arquivo {min(a+1, t)} de {t}" if t else "")
        elif tipo == "pa":
            self.pb_arq.set(max(0.0, min(1.0, item[1])))
        elif tipo == "fim_arq":
            fr: FileResult = item[1]
            self._n_proc += 1
            if fr.status == "erro":
                self._n_erro += 1
            if fr.status == "parcial" or fr.contar_confianca(CONF_NAO_IDENT) > 0 \
                    or fr.contar_confianca("Baixa") > 0:
                self._n_conf += 1
            self._atualizar_contadores()
        elif tipo == "fim":
            self._finalizar(item[1], item[2])

    def _atualizar_contadores(self):
        self.lbl_cont.configure(
            text=f"Processados: {self._n_proc} | Erros: {self._n_erro} | Conferir: {self._n_conf}")

    def _finalizar(self, resultados, saida):
        self.btn_proc.configure(state="normal")
        self.btn_canc.configure(state="disabled")
        self.pb_arq.set(1.0)
        if self.cancel_event.is_set():
            self.lbl_arq.configure(text="Cancelado.")
        else:
            self.pb_geral.set(1.0)
            self.lbl_arq.configure(text="Concluido.")
        self.lbl_etapa.configure(text="")
        self._log(f"=== Fim. {len(resultados)} arquivo(s). Saidas em: {saida} ===")
        if self.v_abrir.get() and resultados and not self.cancel_event.is_set():
            try:
                import os
                os.startfile(saida)  # Windows
            except Exception:  # noqa: BLE001
                pass

    def _log(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _ao_fechar(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Sair", "Processamento em andamento. Cancelar e sair?"):
                return
            self.cancel_event.set()
        self.destroy()


def main():
    utils.ensure_dirs()
    utils.setup_logging()
    App().mainloop()


if __name__ == "__main__":
    main()
