# qforge_studio.py

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from lark import Lark, exceptions

# Import the compiler AND the GRAMMAR for the Live Linter
from qforge_compiler import compile_qforge, QuantumCompileError, GRAMMAR

# =======================================================
# 1. IDE LINE NUMBERS WIDGET
# =======================================================
class TextLineNumbers(tk.Canvas):
    def __init__(self, *args, **kwargs):
        tk.Canvas.__init__(self, *args, **kwargs)
        self.textwidget = None

    def attach(self, text_widget):
        self.textwidget = text_widget

    def redraw(self, *args):
        self.delete("all")
        i = self.textwidget.index("@0,0")
        while True:
            dline = self.textwidget.dlineinfo(i)
            if dline is None: break
            y = dline[1]
            linenum = str(i).split(".")[0]
            self.create_text(2, y, anchor="nw", text=linenum, fill="#858585", font=("Consolas", 12))
            i = self.textwidget.index("%s+1line" % i)

# =======================================================
# 2. CORE APPLICATION STUDIO
# =======================================================
class QForgeStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("QForge Studio - Advanced Quantum IDE")
        self.root.geometry("1400x900")
        self.root.configure(bg="#1e1e1e")

        # Core State
        self.current_file = None
        self.current_folder = None
        self.current_circuit = None
        self.linter_parser = Lark(GRAMMAR, start='start', parser='lalr')
        self.lint_timer = None

        # Theme Colors
        self.colors = {
            "bg": "#1e1e1e",
            "sidebar": "#252526",
            "panel": "#2d2d2d",
            "text": "#d4d4d4",
            "keyword": "#569cd6",    
            "function": "#dcdcaa",   
            "string": "#ce9178",     
            "comment": "#6a9955",    
            "decorator": "#c586c0",  
            "error": "#f44747"       
        }

        self.setup_ui()
        self.setup_canvas_bindings()
        self.setup_tags()

    def setup_ui(self):
        # ---------------------------------------------------
        # TOP TOOLBAR
        # ---------------------------------------------------
        toolbar = tk.Frame(self.root, bg=self.colors["panel"], height=45)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        btn_style = {"bg": "#3c3c3c", "fg": "#cccccc", "activebackground": "#505050", "activeforeground": "white", "relief": tk.FLAT, "bd": 0, "font": ("Segoe UI", 9)}
        
        tk.Button(toolbar, text="📁 Open Folder", command=self.open_folder, **btn_style).pack(side=tk.LEFT, padx=5, pady=8, ipadx=5, ipady=2)
        tk.Button(toolbar, text="📄 New", command=self.new_file, **btn_style).pack(side=tk.LEFT, padx=5, pady=8, ipadx=5, ipady=2)
        tk.Button(toolbar, text="💾 Save", command=self.save_file, **btn_style).pack(side=tk.LEFT, padx=5, pady=8, ipadx=5, ipady=2)
        
        # Separator
        tk.Frame(toolbar, width=2, bg="#555555").pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=8)

        # Execution Options
        tk.Button(toolbar, text="🛠 COMPILE", command=self.compile_only, bg="#444444", fg="white", activebackground="#555", relief=tk.FLAT, bd=0, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=5, pady=8, ipadx=10, ipady=2)
        tk.Button(toolbar, text="▶ RUN QASM", command=self.run_qasm, bg="#d97706", fg="white", activebackground="#f59e0b", relief=tk.FLAT, bd=0, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=5, pady=8, ipadx=10, ipady=2)
        tk.Button(toolbar, text="📊 EXECUTE ALL GRAPHS", command=self.execute_all_graphs, bg="#0e639c", fg="white", activebackground="#1177bb", relief=tk.FLAT, bd=0, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=5, pady=8, ipadx=10, ipady=2)

        # ---------------------------------------------------
        # MAIN PANED WINDOW (3 Split panes)
        # ---------------------------------------------------
        self.main_panes = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=self.colors["bg"], bd=0, sashwidth=4)
        self.main_panes.pack(fill=tk.BOTH, expand=True)

        # 1. LEFT PANEL: Workspace Explorer
        workspace_panel = tk.Frame(self.main_panes, bg=self.colors["sidebar"])
        self.main_panes.add(workspace_panel, minsize=200)
        
        tk.Label(workspace_panel, text=" EXPLORER", bg=self.colors["sidebar"], fg="#cccccc", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill=tk.X, pady=5)
        
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=self.colors["sidebar"], foreground=self.colors["text"], fieldbackground=self.colors["sidebar"], borderwidth=0, font=("Segoe UI", 10))
        style.map("Treeview", background=[("selected", "#094771")], foreground=[("selected", "white")])
        
        self.tree = ttk.Treeview(workspace_panel, show="tree")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # 2. MIDDLE PANEL: Code Editor & Console
        editor_panel = tk.Frame(self.main_panes, bg=self.colors["bg"])
        self.main_panes.add(editor_panel, minsize=450)

        editor_container = tk.Frame(editor_panel, bg=self.colors["bg"])
        editor_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.line_numbers = TextLineNumbers(editor_container, width=35, bg=self.colors["bg"], bd=0, highlightthickness=0)
        self.line_numbers.pack(side=tk.LEFT, fill=tk.Y)
        
        self.editor = tk.Text(editor_container, bg=self.colors["bg"], fg=self.colors["text"], font=("Consolas", 12), insertbackground="white", undo=True, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground="#333333")
        self.editor.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.line_numbers.attach(self.editor)
        
        self.editor.bind("<KeyRelease>", self.on_editor_change)
        self.editor.bind("<MouseWheel>", self.on_editor_change)

        tk.Label(editor_panel, text=" TERMINAL & LINTER", bg=self.colors["bg"], fg="#cccccc", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill=tk.X)
        self.console = tk.Text(editor_panel, bg="#000000", fg="#4af626", font=("Consolas", 10), height=8, state=tk.DISABLED, relief=tk.FLAT, bd=0)
        self.console.pack(fill=tk.X, padx=5, pady=(0, 5))

        # 3. RIGHT PANEL: Visual Composer / QASM Viewer
        self.right_panel = tk.Frame(self.main_panes, bg=self.colors["sidebar"])
        self.main_panes.add(self.right_panel, minsize=500)

        palette = tk.Frame(self.right_panel, bg="#333333", height=40)
        palette.pack(side=tk.TOP, fill=tk.X)
        
        pal_style = {"fg": "white", "relief": tk.FLAT, "bd": 0, "font": ("Segoe UI", 8, "bold")}
        tk.Button(palette, text="[H]", command=lambda: self.insert_code("superpose(q[0])\n"), bg="#007acc", **pal_style).pack(side=tk.LEFT, padx=2, pady=5)
        tk.Button(palette, text="[X]", command=lambda: self.insert_code("flip(q[0])\n"), bg="#009688", **pal_style).pack(side=tk.LEFT, padx=2, pady=5)
        tk.Button(palette, text="[CX]", command=lambda: self.insert_code("entangle(q[0], q[1])\n"), bg="#673ab7", **pal_style).pack(side=tk.LEFT, padx=2, pady=5)
        tk.Button(palette, text="[Meas]", command=lambda: self.insert_code("measure(q)\n"), bg="#c53929", **pal_style).pack(side=tk.LEFT, padx=2, pady=5)
        tk.Button(palette, text="⛶ Fit", command=self.fit_to_screen, bg="#555555", fg="white", relief=tk.FLAT, bd=0).pack(side=tk.RIGHT, padx=5, pady=5)

        self.view_container = tk.Frame(self.right_panel, bg=self.colors["sidebar"])
        self.view_container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.view_container, bg=self.colors["bg"], highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.qasm_viewer = tk.Text(self.view_container, bg="#000000", fg="#d4d4d4", font=("Consolas", 12), state=tk.DISABLED, relief=tk.FLAT, bd=0)

    def setup_tags(self):
        self.editor.tag_configure("keyword", foreground=self.colors["keyword"], font=("Consolas", 12, "bold"))
        self.editor.tag_configure("function", foreground=self.colors["function"])
        self.editor.tag_configure("string", foreground=self.colors["string"])
        self.editor.tag_configure("comment", foreground=self.colors["comment"], font=("Consolas", 12, "italic"))
        self.editor.tag_configure("decorator", foreground=self.colors["decorator"])
        self.editor.tag_configure("error_underline", underline=True, underlinefg=self.colors["error"])

    # ---------------------------------------------------
    # WORKSPACE EXPLORER LOGIC
    # ---------------------------------------------------
    def open_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.current_folder = folder
            self.tree.delete(*self.tree.get_children())
            self.populate_tree("", folder)
            self.log(f"Opened workspace: {folder}")

    def populate_tree(self, parent, path):
        for item in sorted(os.listdir(path)):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                node = self.tree.insert(parent, "end", text=f"📁 {item}", open=False)
                self.populate_tree(node, item_path)
            elif item.endswith('.qf'):
                self.tree.insert(parent, "end", text=f"📄 {item}", values=[item_path])

    def on_tree_select(self, event):
        selected = self.tree.selection()
        if not selected: return
        item = self.tree.item(selected[0])
        if "values" in item and item["values"]:
            self.load_file(item["values"][0])

    def load_file(self, filepath):
        with open(filepath, 'r') as f:
            self.editor.delete(1.0, tk.END)
            self.editor.insert(tk.END, f.read())
        self.current_file = filepath
        self.on_editor_change()
        self.compile_only()

    # ---------------------------------------------------
    # EDITOR, HIGHLIGHTER & LINTER LOGIC
    # ---------------------------------------------------
    def new_file(self):
        self.editor.delete(1.0, tk.END)
        self.current_file = None
        self.canvas.delete("all")
        self.qasm_viewer.config(state=tk.NORMAL)
        self.qasm_viewer.delete(1.0, tk.END)
        self.qasm_viewer.config(state=tk.DISABLED)
        self.log("Started a new file.")
        self.on_editor_change()

    def save_file(self):
        if not self.current_file:
            self.current_file = filedialog.asksaveasfilename(defaultextension=".qf", filetypes=[("QForge Files", "*.qf")])
        if self.current_file:
            with open(self.current_file, 'w') as f:
                f.write(self.editor.get(1.0, tk.END))
            self.log(f"Saved {self.current_file}")

    def insert_code(self, snippet):
        self.editor.insert(tk.INSERT, snippet)
        self.editor.see(tk.INSERT)
        self.editor.focus_set()
        self.on_editor_change()

    def on_editor_change(self, event=None):
        self.line_numbers.redraw()
        self.highlight_syntax()
        if self.lint_timer: self.root.after_cancel(self.lint_timer)
        self.lint_timer = self.root.after(500, self.run_linter)

    def highlight_syntax(self):
        for tag in ["keyword", "function", "string", "comment", "decorator"]:
            self.editor.tag_remove(tag, "1.0", tk.END)
        text = self.editor.get("1.0", tk.END)
        patterns = {
            "keyword": r'\b(if|quantum|classical|for|in|range|with|ancilla|as|end|defcal|def|qregister|cint|qint|param)\b',
            "function": r'\b(superpose|entangle|flip|rotate|measure|play|drive|Gaussian|add|search)\b(?=\()',
            "string": r'".*?"',
            "decorator": r'@[a-zA-Z_]+',
            "comment": r'#.*'
        }
        for tag, pattern in patterns.items():
            for match in re.finditer(pattern, text):
                start, end = f"1.0 + {match.start()} chars", f"1.0 + {match.end()} chars"
                self.editor.tag_add(tag, start, end)

    def run_linter(self):
        self.editor.tag_remove("error_underline", "1.0", tk.END)
        source = self.editor.get("1.0", tk.END).strip()
        if not source: return
        try:
            self.linter_parser.parse(source)
            self.log("[Linter] Clean syntax.")
        except exceptions.UnexpectedCharacters as e:
            self.editor.tag_add("error_underline", f"{e.line}.{e.column - 1}", f"{e.line}.{e.column}")
            self.log(f"[Linter Error] Unexpected character at line {e.line}, col {e.column}", True)
        except exceptions.UnexpectedToken as e:
            self.editor.tag_add("error_underline", f"{e.line}.{e.column - 1}", f"{e.line}.{e.column + len(e.token) - 1}")
            self.log(f"[Linter Error] Unexpected token '{e.token}' at line {e.line}", True)
        except: pass

    def log(self, message, is_error=False):
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.insert(tk.END, message)
        self.console.config(fg=self.colors["error"] if is_error else "#4af626")
        self.console.config(state=tk.DISABLED)

    # ---------------------------------------------------
    # NEW EXECUTION MODES
    # ---------------------------------------------------
    def compile_only(self):
        source = self.editor.get(1.0, tk.END).strip()
        if not source: return
        
        self.qasm_viewer.pack_forget()
        self.canvas.pack(fill=tk.BOTH, expand=True)

        try:
            self.current_circuit = compile_qforge(source, target="qiskit")
            self.log("[Success] Circuit compiled and visualized. Ready for Simulation.")
            self.draw_circuit_blocks()
            self.root.after(100, self.fit_to_screen)
            return True
        except Exception as e:
            self.log(f"Compiler Error: {str(e)}", True)
            self.canvas.delete("all")
            return False

    def run_qasm(self):
        source = self.editor.get(1.0, tk.END).strip()
        if not source: return
        
        self.canvas.pack_forget()
        self.qasm_viewer.pack(fill=tk.BOTH, expand=True)

        try:
            output = compile_qforge(source, target="qasm3")
            self.qasm_viewer.config(state=tk.NORMAL)
            self.qasm_viewer.delete(1.0, tk.END)
            self.qasm_viewer.insert(tk.END, output)
            self.qasm_viewer.config(state=tk.DISABLED)
            self.log("[Success] Hardware Code (OpenQASM 3) generated.")
        except Exception as e:
            self.log(f"Compiler Error: {str(e)}", True)

    def execute_all_graphs(self):
        if self.compile_only():
            try:
                has_measurement = any(inst.operation.name == 'measure' for inst in self.current_circuit.data)
                if has_measurement:
                    self.show_simulation_results()
                else:
                    self.log("[Warning] No 'measure' gates found. Add measure(q) to view probabilities.", True)
            except Exception as e:
                self.log(f"\n[Notice] Sim Skipped: {str(e)}", True)

    # ---------------------------------------------------
    # SIMULATION UI (Global & Single Qubit)
    # ---------------------------------------------------
    def show_simulation_results(self, single_qubit_idx=None):
        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from qiskit.quantum_info import Statevector, partial_trace
            from qiskit.visualization import plot_histogram, plot_bloch_multivector
        except ImportError: return

        sim_win = tk.Toplevel(self.root)
        sim_win.configure(bg="#ffffff")

        try:
            circuit_no_meas = self.current_circuit.copy()
            circuit_no_meas.data = [inst for inst in circuit_no_meas.data if inst.operation.name not in ['measure', 'barrier']]
            
            state = Statevector(circuit_no_meas)
            
            # --- INTERACTIVE SINGLE QUBIT STATE ---
            if single_qubit_idx is not None:
                sim_win.title(f"Quantum State: Qubit {single_qubit_idx}")
                sim_win.geometry("500x500")
                
                # Trace out all OTHER qubits to isolate the density matrix of the clicked one
                all_indices = list(range(self.current_circuit.num_qubits))
                all_indices.remove(single_qubit_idx)
                
                # Protect against 1-qubit circuits crashing partial_trace
                if len(all_indices) == 0:
                    reduced_rho = state
                else:
                    reduced_rho = partial_trace(state, all_indices)
                
                tk.Label(sim_win, text=f"Reduced State (Qubit {single_qubit_idx})", bg="#ffffff", font=("Segoe UI", 14, "bold")).pack(pady=10)
                
                fig_bloch = plot_bloch_multivector(reduced_rho)
                if fig_bloch.axes:
                    fig_bloch.axes[0].set_title(f"qubit {single_qubit_idx}")
                    
                canvas_bloch = FigureCanvasTkAgg(fig_bloch, master=sim_win)
                canvas_bloch.draw()
                canvas_bloch.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                
            # --- GLOBAL STATE ---
            else:
                sim_win.title("Global Quantum Execution Engine")
                sim_win.geometry("900x800")
                probabilities = state.probabilities_dict()

                bloch_frame = tk.Frame(sim_win, bg="#ffffff")
                bloch_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
                tk.Label(bloch_frame, text="Global Qubit States (Bloch Spheres)", bg="#ffffff", font=("Segoe UI", 14, "bold")).pack(pady=10)
                
                fig_bloch = plot_bloch_multivector(state)
                canvas_bloch = FigureCanvasTkAgg(fig_bloch, master=bloch_frame)
                canvas_bloch.draw()
                canvas_bloch.get_tk_widget().pack(fill=tk.BOTH, expand=True)

                hist_frame = tk.Frame(sim_win, bg="#ffffff")
                hist_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)
                tk.Label(hist_frame, text="Global Measurement Probabilities", bg="#ffffff", font=("Segoe UI", 14, "bold")).pack(pady=10)
                
                fig_hist = plt.figure(figsize=(9, 4))
                ax_hist = fig_hist.add_subplot(111)
                plot_histogram(probabilities, ax=ax_hist, color="#0e639c")
                
                canvas_hist = FigureCanvasTkAgg(fig_hist, master=hist_frame)
                canvas_hist.draw()
                canvas_hist.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        except Exception as e:
            self.graceful_fallback_ui(sim_win, str(e))

    def graceful_fallback_ui(self, sim_win, err_msg):
        err_msg = err_msg.lower()
        if "unbound parameters" in err_msg: detail = "This circuit contains UNBOUND PARAMETERS (e.g., theta)."
        elif "control flow" in err_msg: detail = "This circuit contains DYNAMIC CONTROL FLOW (If/Else)."
        elif "cannot apply instruction" in err_msg or "not a unitary" in err_msg: detail = "This circuit contains CUSTOM PULSES."
        else: detail = f"Advanced hardware feature detected:\n{err_msg}"
        tk.Label(sim_win, text="⚠️", bg="#ffffff", fg="#e54331", font=("Segoe UI", 48)).pack(pady=(150, 10))
        tk.Label(sim_win, text="Local Math Simulation Unavailable", bg="#ffffff", fg="#e54331", font=("Segoe UI", 20, "bold")).pack(pady=5)
        tk.Label(sim_win, text=detail, bg="#ffffff", fg="#555555", font=("Segoe UI", 12)).pack(pady=10)
        tk.Label(sim_win, text="Click 'RUN QASM' to export this circuit for physical QPU execution.", bg="#ffffff", fg="#0e639c", font=("Segoe UI", 10, "italic")).pack(pady=20)

    # ---------------------------------------------------
    # CANVAS PAN, ZOOM & INTERACTIVE CLICKS
    # ---------------------------------------------------
    def setup_canvas_bindings(self):
        # UNIFIED EVENT: Handles both Panning and Clicking blocks safely
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", self.do_zoom)
        self.canvas.bind("<Button-4>", self.do_zoom)
        self.canvas.bind("<Button-5>", self.do_zoom)

    def do_zoom(self, event):
        scale_factor = 1.1 if event.num == 4 or event.delta > 0 else 0.9 if event.num == 5 or event.delta < 0 else 1
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.canvas.scale("all", x, y, scale_factor, scale_factor)

    def fit_to_screen(self):
        self.canvas.update_idletasks()
        bbox = self.canvas.bbox("all")
        if not bbox: return
        self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        dw, dh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        scale = min((cw - 40) / dw if dw > 0 else 1, (ch - 40) / dh if dh > 0 else 1, 2.0)
        self.canvas.move("all", -bbox[0], -bbox[1])
        self.canvas.scale("all", 0, 0, scale, scale)
        nb = self.canvas.bbox("all")
        self.canvas.move("all", (cw - (nb[2] - nb[0])) / 2, (ch - (nb[3] - nb[1])) / 2)

    def on_canvas_press(self, event):
        # 1. Register the coordinates in case the user wants to drag/pan the canvas
        self.canvas.scan_mark(event.x, event.y)
        
        # 2. Check if the user is clicking on a specific Measure Block!
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        clicked_items = self.canvas.find_overlapping(x-1, y-1, x+1, y+1)
        
        for item in clicked_items:
            tags = self.canvas.gettags(item)
            if "measure_block" in tags:
                for t in tags:
                    if t.startswith("qubit_"):
                        qubit_idx = int(t.split("_")[1])
                        self.log(f"[Execution] Calculating Partial Trace for Qubit {qubit_idx}...")
                        self.show_simulation_results(single_qubit_idx=qubit_idx)
                        return # Stop checking, pop the window!

    def draw_circuit_blocks(self):
        self.canvas.delete("all")
        if not self.current_circuit: return

        x_start, y_start, wire_spacing, gate_spacing = 50, 50, 60, 60
        qubit_y_coords = {}
        qubit_index_map = {}
        
        for i, q in enumerate(self.current_circuit.qubits):
            y = y_start + (i * wire_spacing)
            qubit_y_coords[q] = y
            reg_name = "q"
            for reg in self.current_circuit.qregs:
                if q in reg:
                    reg_name = f"{reg.name}[{reg.index(q)}]"
                    qubit_index_map[q] = self.current_circuit.qubits.index(q)
                    break
            self.canvas.create_text(x_start - 15, y, text=reg_name, anchor="e", font=("Consolas", 10, "bold"), fill="#aaaaaa")
            self.canvas.create_line(x_start, y, 2500, y, fill="#555555", width=2)

        current_x = x_start + 40
        for instruction in self.current_circuit.data:
            gate, qargs = instruction.operation, instruction.qubits
            if not qargs: continue
            
            color = "#5c5c5c" 
            if gate.name == "h": color = "#007acc"
            elif gate.name == "x": color = "#009688"
            elif gate.name in ["rx", "ry", "rz"]: color = "#d97706"
            elif gate.name in ["cx", "mcx"]: color = "#673ab7"
            elif gate.name == "measure": color = "#c53929"

            if len(qargs) > 1:
                y_coords = [qubit_y_coords[q] for q in qargs]
                self.canvas.create_line(current_x, min(y_coords), current_x, max(y_coords), fill=color, width=3)
                for q in qargs[:-1]: self.canvas.create_oval(current_x - 6, qubit_y_coords[q] - 6, current_x + 6, qubit_y_coords[q] + 6, fill=color, outline=color)
                targ_y = qubit_y_coords[qargs[-1]]
                self.canvas.create_rectangle(current_x - 15, targ_y - 15, current_x + 15, targ_y + 15, fill=color, outline="#252526", width=2)
                self.canvas.create_text(current_x, targ_y, text="X", fill="white", font=("Segoe UI", 10, "bold"))
            else:
                y = qubit_y_coords[qargs[0]]
                label = gate.name.upper()
                if hasattr(gate, 'params') and gate.params:
                    param_val = str(gate.params[0])[:5] if len(str(gate.params[0])) > 5 else str(gate.params[0])
                    label = f"{label}({param_val})"
                
                box_width = 25 if len(label) < 4 else 35
                
                # --- INTERACTIVE MEASUREMENT BLOCKS ---
                if gate.name == "measure":
                    q_idx = qubit_index_map.get(qargs[0], 0)
                    tag = f"qubit_{q_idx}"
                    # Tag both the rectangle and the text so clicking anywhere works
                    self.canvas.create_rectangle(current_x - box_width, y - 18, current_x + box_width, y + 18, fill=color, outline="#ffaaaa", width=2, tags=("measure_block", tag))
                    self.canvas.create_text(current_x, y, text=label, fill="white", font=("Segoe UI", 9, "bold"), tags=("measure_block", tag))
                    # Hover text to hint that it is clickable
                    self.canvas.create_text(current_x, y - 25, text="Click to Inspect", fill="#aaaaaa", font=("Segoe UI", 7, "italic"))
                else:
                    self.canvas.create_rectangle(current_x - box_width, y - 18, current_x + box_width, y + 18, fill=color, outline="#252526", width=2)
                    self.canvas.create_text(current_x, y, text=label, fill="white", font=("Segoe UI", 9, "bold"))
                    
            current_x += gate_spacing

if __name__ == "__main__":
    root = tk.Tk()
    app = QForgeStudio(root)
    root.mainloop()
