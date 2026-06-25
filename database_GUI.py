from database_classes import *
from datetime import datetime
import colorsys
import threading

# -------- GUI + Tree persistence utilities --------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TREE_STORAGE_DIR = os.path.join(BASE_DIR, "databases")
os.makedirs(TREE_STORAGE_DIR, exist_ok=True)

# ---------- Reflection / dynamic class discovery ----------

def get_sample_classes():
    current_globals = globals()
    base = current_globals.get("Sample")
    if base is None:
        return {}
    result = {}
    for name, obj in current_globals.items():
        if inspect.isclass(obj) and issubclass(obj, base) and obj is not base:
            result[name] = obj
    return result


# ---------- Permitted children resolution ----------

def resolve_permitted_children(sample_obj):
    out = []
    pcs = getattr(sample_obj, "permitted_children", [])
    for c in pcs:
        if inspect.isclass(c):
            out.append(c.__name__)
        else:
            out.append(str(c))
    return sorted(out, key=lambda s: s.lower())

# ---------- Serialization ----------

def serialize_tree(tree, filename, sort_mode=None):
    ''' Save a tree to JSON file '''
    data = {}
    nodes = []
    for n in tree.all_nodes_itr():
        if n.tag == "SYSTEM":
            data["root"] = {
                "id": n.identifier,
                "sample_system": n.data.get("Sample_System"),
                "sort_mode": sort_mode or n.data.get("sort_mode", "none")
            }
        else:
            obj = n.data["obj"]
            nodes.append({
                "id": n.identifier,
                "parent": tree.parent(n.identifier).identifier if tree.parent(n.identifier) else None,
                "class": obj.__class__.__name__,
                "id": obj.id,
                "entry_created_date": obj.entry_created_date,
                "properties": obj.properties
            })
    data["nodes"] = nodes
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return filename

def get_class_schema(class_name):
    try:
        with open(DATABASE_STRUCTURE_FILE, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        cls_schema = schema.get(class_name, {"required": [], "custom": []})
        return cls_schema.get("required", []), cls_schema.get("custom", [])
    except Exception:
        return [], []

def deserialize_tree(filename):
    ''' Load a tree from JSON file '''
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    tree = treelib.Tree()
    root_id = data["root"]["id"]
    sort_mode = data["root"].get("sort_mode", "none")
    tree.create_node(tag="SYSTEM", identifier=root_id, data={"Sample_System": data["root"]["sample_system"], "sort_mode": sort_mode})
    classes = get_sample_classes()
    # Temporarily suppress key logging to avoid duplicates on load
    for node_spec in data["nodes"]:
        cls_name = node_spec["class"]
        cls = classes.get(cls_name)
        if cls is None:
            continue
        original_log = cls.log_keys
        cls.log_keys = lambda *args, **kwargs: None
        obj = cls(**deepcopy(node_spec["properties"]))
        cls.log_keys = original_log
        # Override immutable fields
        obj._id = node_spec["id"]
        obj._entry_created_date = node_spec["entry_created_date"]
        tree.create_node(tag=cls_name,
                         identifier=obj.id,
                         parent=node_spec["parent"],
                         data={"obj": obj})
    return tree

# ---------- Utility to build kwargs via dialog ----------

class PropertyEditor(tk.Toplevel):
    def __init__(self, master, sample_class, existing_keys, required_props):
        super().__init__(master)
        self.title(f"Properties for {sample_class.__name__}")
        self.sample_class = sample_class
        self.result = None
        self.req = required_props
        self.geometry("420x380")
        self.resizable(False, True)

        self.prop_frame = ttk.Frame(self)
        self.prop_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.rows = []  # (key_var, val_var, key_cb)
        ttk.Label(self.prop_frame, text="Required properties:").grid(row=0, column=0, columnspan=3, sticky="w")

        self.existing_keys = sorted(existing_keys)
        self.existing_keys.insert(0, "<New property...>")
        r = 1
        for rp in self.req:
            kv = tk.StringVar(value=rp)
            vv = tk.StringVar()
            cb = ttk.Combobox(self.prop_frame, values=[rp], textvariable=kv, state="readonly", width=22)
            cb.grid(row=r, column=0, padx=2, pady=2, sticky="w")
            ent = ttk.Entry(self.prop_frame, textvariable=vv, width=28)
            ent.grid(row=r, column=1, padx=2, pady=2, sticky="w")
            self.rows.append((kv, vv, cb))
            r += 1

        ttk.Separator(self.prop_frame).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        r += 1
        ttk.Label(self.prop_frame, text="Optional properties:").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        self.opt_container = ttk.Frame(self.prop_frame)
        self.opt_container.grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1

        btn_add = ttk.Button(self.prop_frame, text="Add property row", command=self.add_optional_row)
        btn_add.grid(row=r, column=0, sticky="w", pady=6)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=4)
        ttk.Button(btn_frame, text="OK", command=self.on_ok).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side="left", padx=5)

        self.add_optional_row()

    def add_optional_row(self):
        # Determine next available row index inside the optional container by inspecting existing widgets' rows
        existing_rows = [int(w.grid_info().get('row', 0)) for w in self.opt_container.grid_slaves()]
        row_index = max(existing_rows) + 1 if existing_rows else 0

        kv = tk.StringVar()
        vv = tk.StringVar()
        cb = ttk.Combobox(self.opt_container, values=self.existing_keys, textvariable=kv, width=22)
        cb.grid(row=row_index, column=0, padx=2, pady=2, sticky="w")
        ent = ttk.Entry(self.opt_container, textvariable=vv, width=28)
        ent.grid(row=row_index, column=1, padx=2, pady=2, sticky="w")

        def on_select(_event):
            if kv.get() == "<New property...>":
                new_key = simpledialog.askstring("New property", "Enter new property name:", parent=self)
                if new_key:
                    # Insert before sentinel if not already present
                    if new_key not in self.existing_keys:
                        self.existing_keys.append(new_key)
                    kv.set(new_key)
                    # Update all combobox values to include the new key
                    for rset in self.rows:
                        try:
                            rset[2]['values'] = self.existing_keys
                        except Exception:
                            pass
                    for child in self.opt_container.winfo_children():
                        if isinstance(child, ttk.Combobox):
                            child['values'] = self.existing_keys

        cb.bind("<<ComboboxSelected>>", on_select)
        self.rows.append((kv, vv, cb))

    def on_ok(self):
        props = {}
        # Collect required
        for kv, vv, _ in self.rows:
            k = kv.get().strip()
            v = vv.get()
            if not k:
                continue
            if k in props:
                messagebox.showerror("Error", f"Duplicate property key: {k}")
                return
            props[k] = v
        # Validate required
        missing = [r for r in self.req if r not in props or props[r] == ""]
        if missing:
            messagebox.showerror("Missing", f"Missing required properties: {', '.join(missing)}")
            return
        self.result = props
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()

# ---------- Main GUI ----------


class AddClassDialog(tk.Toplevel):
    def __init__(self, master, browser):
        super().__init__(master)
        self.title("Add New Structure")
        self.geometry("400x400")
        self.grab_set()
        self.browser = browser

        ttk.Label(self, text="Warning: Samples and processing steps cannot be deleted.\nBe careful not to clutter your database structure.", foreground="red", justify="center").pack(pady=10)

        form = ttk.Frame(self)
        form.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Label(form, text="Class Name:").grid(row=0, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Base Class:").grid(row=1, column=0, sticky="w", pady=5)
        self.base_var = tk.StringVar(value="Sample")
        ttk.Combobox(form, textvariable=self.base_var, values=["Sample", "Processing_Step"], state="readonly").grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(form, text="Required Properties (comma-separated):").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10,0))
        self.req_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.req_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)

        lists_frame = ttk.Frame(form)
        lists_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=10)
        lists_frame.columnconfigure(0, weight=1)
        lists_frame.columnconfigure(1, weight=1)

        ttk.Label(lists_frame, text="Permitted Children:").grid(row=0, column=0, sticky="w")
        self.children_list = tk.Listbox(lists_frame, selectmode="multiple", height=6)
        self.children_list.grid(row=1, column=0, sticky="nsew", padx=(0, 5))

        ttk.Label(lists_frame, text="Permitted Parents:").grid(row=0, column=1, sticky="w")
        self.parents_list = tk.Listbox(lists_frame, selectmode="multiple", height=6)
        self.parents_list.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

        # Populate children list
        import json
        with open(DATABASE_STRUCTURE_FILE, "r", encoding="utf-8") as f:
            self.schema = json.load(f)
        classes = sorted([c for c in self.schema.keys() if c not in ['Sample', 'Processing_Step']])
        for c in classes:
            self.children_list.insert(tk.END, c)
            self.parents_list.insert(tk.END, c)

        form.columnconfigure(1, weight=1)
        form.rowconfigure(4, weight=1)

        ttk.Button(self, text="Create", command=self.create_class).pack(pady=10)

    def create_class(self):
        name = self.name_var.get().strip()
        base = self.base_var.get().strip()
        reqs = [r.strip() for r in self.req_var.get().split(',') if r.strip()]
        
        sel_children = self.children_list.curselection()
        perm_children = [self.children_list.get(i) for i in sel_children]
        
        sel_parents = self.parents_list.curselection()
        perm_parents = [self.parents_list.get(i) for i in sel_parents]

        if not name:
            messagebox.showwarning("Input Error", "Class name cannot be empty.")
            return
        if not name.isidentifier():
            messagebox.showwarning("Input Error", "Class name must be a valid identifier (no spaces).")
            return
        if name in self.schema:
            messagebox.showwarning("Input Error", "Class already exists.")
            return

        self.schema[name] = {
            "base": base,
            "required": reqs,
            "custom": [],
            "permitted_children": perm_children
        }
        
        for parent in perm_parents:
            if name not in self.schema[parent].get("permitted_children", []):
                self.schema[parent].setdefault("permitted_children", []).append(name)

        import json
        with open(DATABASE_STRUCTURE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.schema, f, indent=4)

        import database_classes
        if hasattr(database_classes, "create_class_from_schema"):
            database_classes.create_class_from_schema(name)
            
        if self.browser: self.browser.populate_hierarchy()
        self.destroy()
        messagebox.showinfo("Success", f"Class {name} created successfully.")


class StructureBrowser(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Structure Browser")
        self.geometry("900x600")
        self.grab_set()

        main_paned = ttk.PanedWindow(self, orient="horizontal")
        main_paned.pack(fill="both", expand=True, padx=10, pady=10)

        # Left pane: Class hierarchy
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)

        ttk.Label(left_frame, text="Class Hierarchy", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 5))
        
        self.tree = ttk.Treeview(left_frame, show="tree")
        self.tree.pack(fill="both", expand=True)
        sb1 = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb1.set)
        sb1.pack(side="right", fill="y")
        
        self.tree.bind("<<TreeviewSelect>>", self.on_class_select)

        # Right pane: Properties
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=2)
        
        ttk.Label(right_frame, text="Permitted Children", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.perm_list = tk.Listbox(right_frame, height=5)
        self.perm_list.pack(fill="x", pady=(0, 10))

        ttk.Label(right_frame, text="Required Properties", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.req_list = tk.Listbox(right_frame, height=5)
        self.req_list.pack(fill="x", pady=(0, 10))

        ttk.Label(right_frame, text="Custom Properties", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.cust_list = tk.Listbox(right_frame, height=8)
        self.cust_list.pack(fill="both", expand=True, pady=(0, 5))

        add_frame = ttk.Frame(right_frame)
        add_frame.pack(fill="x", pady=5)
        
        self.new_prop_var = tk.StringVar()
        self.prop_entry = ttk.Entry(add_frame, textvariable=self.new_prop_var)
        self.prop_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        ttk.Button(add_frame, text="Add Property", command=self.add_custom_property).pack(side="left")
        

        self.populate_hierarchy()
        
    def populate_hierarchy(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
            
        import json
        try:
            with open(DATABASE_STRUCTURE_FILE, 'r', encoding='utf-8') as f:
                schema = json.load(f)
        except:
            schema = {}
            
        samp_iid = self.tree.insert("", "end", text="Samples", iid="Samples")
        proc_iid = self.tree.insert("", "end", text="Processing Steps", iid="Processing Steps")
        self.tree.item(samp_iid, open=True)
        self.tree.item(proc_iid, open=True)
        
        for name, config in schema.items():
            if name in ['Sample', 'Processing_Step']: continue
            base = config.get("base")
            if base == "Processing_Step":
                self.tree.insert(proc_iid, "end", text=name, iid=name)
            else:
                self.tree.insert(samp_iid, "end", text=name, iid=name)

    def on_class_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        cls_name = sel[0]
        if cls_name in ["Samples", "Processing Steps"]: return
        
        self.req_list.delete(0, tk.END)
        self.cust_list.delete(0, tk.END)
        self.perm_list.delete(0, tk.END)
        
        import json
        with open(DATABASE_STRUCTURE_FILE, 'r', encoding='utf-8') as f:
            schema = json.load(f)
            
        config = schema.get(cls_name, {})
        for r in config.get("required", []): self.req_list.insert(tk.END, r)
        for c in config.get("custom", []): self.cust_list.insert(tk.END, c)
        for p in config.get("permitted_children", []): self.perm_list.insert(tk.END, p)
            
    def add_custom_property(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a class first.")
            return
            
        cls_name = sel[0]
        if cls_name in ["Samples", "Processing Steps"]: return
        
        new_prop = self.new_prop_var.get().strip()
        if not new_prop: return
        
        import json
        try:
            with open(DATABASE_STRUCTURE_FILE, "r", encoding="utf-8") as f:
                schema = json.load(f)
                    
            if new_prop not in schema[cls_name]["custom"] and new_prop not in schema[cls_name]["required"]:
                schema[cls_name]["custom"].append(new_prop)
                with open(DATABASE_STRUCTURE_FILE, "w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=4)
                    
                self.cust_list.insert(tk.END, new_prop)
                self.new_prop_var.set("")
            else:
                messagebox.showinfo("Exists", "Property already exists.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add property: {e}")

    def open_advanced(self):
        AddClassDialog(self, self)

class SampleTreeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Sample Tree Manager - {TREE_STORAGE_DIR}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.tree_obj = None
        self.current_file = None
        self.discover_btn = None
        self.display_mode = "single"
        self.multi_trees = {}
        self.treeview_index = {}
        self.treeview_system_iids = {}
        self.sort_mode = "none"
        self.sort_state = {}
        self.properties_panel = None
        self.properties_panel_tree = None
        self.last_action_was_save_archive_close = False
        
        # Menu Bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Tree", command=self.create_new_tree)
        file_menu.add_command(label="Load Tree", command=self.load_tree)
        file_menu.add_command(label="Load Multiple Trees", command=self.load_multiple_trees)
        file_menu.add_separator()
        file_menu.add_command(label="Save Tree", command=self.save_tree)
        file_menu.add_command(label="Save, archive and close", command=self._save_archive_and_close)
        file_menu.add_command(label="Close Selected Tree", command=self.close_selected_tree)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing)
        menubar.add_cascade(label="File", menu=file_menu)
        
        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Toggle Rainbow Mode", command=self.toggle_rainbow_mode)
        menubar.add_cascade(label="View", menu=view_menu)
        
        # Tools Menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Search", command=self.search_property)
        tools_menu.add_command(label="Structure Browser", command=self.open_structure_browser)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        # Advanced Menu
        advanced_menu = tk.Menu(menubar, tearoff=0)
        advanced_menu.add_command(label="Add New Structure", command=lambda: AddClassDialog(self.root, None))
        advanced_menu.add_command(label="Import Legacy Keys", command=self.import_legacy_keys)
        menubar.add_cascade(label="Advanced", menu=advanced_menu)
        
        # Check for restored schema
        import database_classes
        if getattr(database_classes, "RESTORED_DEFAULT_SCHEMA", False):
            def show_warn():
                messagebox.showwarning("Default Structure Restored", "database_structure.json was missing.\n\nA default structure has been recreated.", parent=self.root)
            self.root.after(1000, show_warn)

        main = ttk.Frame(root)
        main.pack(fill="both", expand=True, padx=6, pady=6)



        # 3. Main Split View
        paned_main = ttk.PanedWindow(main, orient="horizontal")
        paned_main.pack(fill="both", expand=True, pady=6)

        treeview_frame = ttk.Frame(paned_main)
        self.treeview = ttk.Treeview(treeview_frame)
        self.treeview.pack(side="left", fill="both", expand=True)
        treeview_scrollbar = ttk.Scrollbar(treeview_frame, orient="vertical", command=self.treeview.yview)
        treeview_scrollbar.pack(side="right", fill="y")
        self.treeview.configure(yscrollcommand=treeview_scrollbar.set)
        paned_main.add(treeview_frame, weight=3)

        self.properties_panel = ttk.Frame(paned_main)
        ttk.Label(self.properties_panel, text="Properties", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=4, pady=4)
        properties_frame = ttk.LabelFrame(self.properties_panel, text="Node Properties")
        properties_frame.pack(fill="both", expand=True, padx=4, pady=2)
        self.properties_panel_tree = ttk.Treeview(
            properties_frame,
            columns=("property", "value"),
            show="tree",
            displaycolumns=("property", "value"),
        )
        self.properties_panel_tree.column("#0", width=0, stretch=False)
        self.properties_panel_tree.column("property", width=110, anchor="w")
        self.properties_panel_tree.column("value", width=180, anchor="w")
        try:
            self.properties_panel_tree.tag_configure("section_header", font=("TkDefaultFont", 9, "bold"))
            self.properties_panel_tree.tag_configure("filepath", foreground="#0066cc", font=("TkDefaultFont", 9, "underline"))
        except Exception:
            pass
        properties_sb = ttk.Scrollbar(properties_frame, orient="vertical", command=self.properties_panel_tree.yview)
        self.properties_panel_tree.configure(yscrollcommand=properties_sb.set)
        properties_sb.pack(side="right", fill="y")
        self.properties_panel_tree.pack(fill="both", expand=True)
        self.properties_panel_tree.bind("<Double-1>", self.on_property_double_click)
        
        paned_main.add(self.properties_panel, weight=2)

        self.treeview.bind("<<TreeviewSelect>>", self.on_select)
        self.treeview.bind("<<TreeviewOpen>>", self.on_treeview_open)

        # 1. Quick Access Top Bar
        top_bar = ttk.Frame(main)
        top_bar.pack(fill="x", pady=(0, 2))
        ttk.Button(top_bar, text="Collapse All", command=self.collapse_all_trees).pack(side="left", padx=2)
        ttk.Button(top_bar, text="Expand All", command=self.expand_all_trees).pack(side="left", padx=2)
        ttk.Button(top_bar, text="Search", command=self.search_property).pack(side="left", padx=2)
        ttk.Button(top_bar, text="Save Tree", command=self.save_tree).pack(side="left", padx=2)
        ttk.Button(top_bar, text="Save, archive and close", command=self._save_archive_and_close).pack(side="left", padx=2)
        
        # Hidden Rainbow Button for backward compatibility if needed by mode changes
        self.rainbow_active = False

        # 2. Sorting Frame
        sort_frame = ttk.Frame(main)
        sort_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(sort_frame, text="Sort by:").pack(side="left", padx=2)
        self.sort_var = tk.StringVar(value="none")
        ttk.Radiobutton(sort_frame, text="None", variable=self.sort_var, value="none", command=self.on_sort_changed).pack(side="left")
        ttk.Radiobutton(sort_frame, text="Name", variable=self.sort_var, value="name", command=self.on_sort_changed).pack(side="left")
        ttk.Radiobutton(sort_frame, text="Date Created", variable=self.sort_var, value="date_created", command=self.on_sort_changed).pack(side="left")

        # 4. Action Frame (Add Child / Node Actions)
        action_frame = ttk.LabelFrame(main, text="Selected Node Actions")
        action_frame.pack(fill="x", pady=4)

        ttk.Label(action_frame, text="Parent Node:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.parent_label_var = tk.StringVar(value="-")
        ttk.Label(action_frame, textvariable=self.parent_label_var).grid(row=0, column=1, sticky="w", padx=2, pady=2)

        ttk.Label(action_frame, text="Child Class:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.class_var = tk.StringVar()
        self.class_cb = ttk.Combobox(action_frame, textvariable=self.class_var, width=28, state="readonly")
        self.class_cb.grid(row=1, column=1, sticky="w", padx=2, pady=2)

        ttk.Button(action_frame, text="Edit Node", command=self.edit_node).grid(row=2, column=0, padx=2, pady=6, sticky="w")
        ttk.Button(action_frame, text="Copy Node", command=self.copy_node).grid(row=2, column=1, padx=2, pady=6, sticky="w")
        ttk.Button(action_frame, text="Create Node", command=self.add_child_node).grid(row=2, column=2, padx=2, pady=6, sticky="w")

        # 5. Status Bar
        status_frame = ttk.Frame(main)
        status_frame.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar()
        ttk.Label(status_frame, textvariable=self.status_var, relief="sunken", anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_frame, text=f"Workspace: {TREE_STORAGE_DIR}", relief="sunken", anchor="e").pack(side="right")

        self.refresh_status("Ready")

        cache_file = os.path.join(BASE_DIR, ".db_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    import json
                    cache = json.load(f)
                    last_tree = cache.get("last_tree")
                    if last_tree and os.path.exists(last_tree):
                        self._load_specific_tree(last_tree)
            except Exception:
                pass


    def open_structure_browser(self):
        StructureBrowser(self.root)

    def refresh_status(self, msg):
        self.status_var.set(msg)

    def on_sort_changed(self):
        """Called when sort mode changes"""
        if self.display_mode == "single":
            self.sort_mode = self.sort_var.get()
            self.populate_treeview()
        elif self.display_mode == "multi":
            for system_key in self.multi_trees.keys():
                self.sort_state[system_key] = self.sort_var.get()
            self.populate_multi_treeview()

    def get_sort_children(self, tree, parent_id):
        """Get children of a node sorted according to current sort_mode"""
        children = tree.children(parent_id)
        sort_mode = self.sort_mode if self.display_mode == "single" else self.sort_var.get()
        
        if sort_mode == "none":
            return children
        elif sort_mode == "name":
            return sorted(children, key=lambda node: node.tag.lower())
        elif sort_mode == "date_created":
            def get_date(node):
                try:
                    obj = node.data.get("obj")
                    if obj:
                        return obj.entry_created_date or ""
                except Exception:
                    pass
                return ""
            return sorted(children, key=get_date)
        return children

    def update_properties_panel(self, ctx):
        """Update the properties panel with current node's properties"""
        for child in self.properties_panel_tree.get_children():
            self.properties_panel_tree.delete(child)
        
        if not ctx or not ctx.get("node"):
            self.properties_panel_tree.insert("", "end", values=("No node selected", ""))
            return
        
        node = ctx["node"]
        if node.tag == "SYSTEM":
            self.properties_panel_tree.insert("", "end", values=("Type", "System Root"))
            self.properties_panel_tree.insert("", "end", values=("Sample System", node.data.get("Sample_System", "")))
            return
        
        try:
            obj = node.data.get("obj")
            if not obj:
                return
            
            self.properties_panel_tree.insert("", "end", values=("Type", obj.__class__.__name__))
            self.properties_panel_tree.insert("", "end", values=("ID", "" if getattr(obj, "id", None) is None else str(obj.id)))
            self.properties_panel_tree.insert("", "end", values=("Created", "" if getattr(obj, "entry_created_date", None) is None else str(obj.entry_created_date)))
            self.properties_panel_tree.insert("", "end", values=("Custom Properties", ""), tags=("section_header",))
            
            props = getattr(obj, "properties", {})
            if isinstance(props, dict):
                for k in sorted(props.keys(), key=lambda s: s.lower()):
                    v = props.get(k, None)
                    try:
                        display_v = "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                    except Exception:
                        display_v = str(v)
                    
                    tags = ()
                    if display_v and isinstance(display_v, str) and os.path.exists(display_v):
                        tags = ("filepath",)
                    self.properties_panel_tree.insert("", "end", values=(k, display_v), tags=tags)
        except Exception as e:
            self.properties_panel_tree.insert("", "end", values=("Error", str(e)))

    def on_property_double_click(self, event):
        """Handle double-click on properties to open file/folder paths"""
        item_id = self.properties_panel_tree.focus()
        if not item_id:
            return
        
        values = self.properties_panel_tree.item(item_id, "values")
        if len(values) < 2:
            return
        
        prop_value = values[1].strip()
        if prop_value and os.path.exists(prop_value):
            try:
                # Open directory in Explorer, file in default viewer natively on Windows
                os.startfile(prop_value)
                self.refresh_status(f"Opened path: {prop_value}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open path: {e}", parent=self.root)

    def copy_node(self):
        """Copy the currently selected node to a new destination"""
        ctx = self._selected_node_context()
        if not ctx:
            messagebox.showwarning("Select", "Select a node to copy first.")
            return
        
        if ctx["is_system_root"]:
            messagebox.showwarning("Not Allowed", "Cannot copy the SYSTEM root node.")
            return
        
        tree_obj = ctx["tree"]
        node = ctx["node"]
        obj = node.data.get("obj")
        if not obj:
            messagebox.showerror("Error", "Node has no object data.")
            return
        
        class_name = node.tag
        classes = get_sample_classes()
        cls = classes.get(class_name)
        if not cls:
            messagebox.showerror("Error", f"Class {class_name} not found.")
            return
        
        # Determine required properties from file
        try:
            with open(REQUIRED_PROPERTIES_FILE, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                if not txt:
                    subclass_req_map = {}
                else:
                    try:
                        subclass_req_map = json.loads(txt)
                        if not isinstance(subclass_req_map, dict):
                            raise ValueError("JSON root is not an object")
                    except Exception:
                        subclass_req_map = {}
                        for line in txt.splitlines():
                            line = line.split("#", 1)[0].strip()
                            if not line:
                                continue
                            if ":" in line:
                                cls_key, vals = line.split(":", 1)
                            elif "=" in line:
                                cls_key, vals = line.split("=", 1)
                            else:
                                parts = line.split()
                                if len(parts) == 2:
                                    cls_key, vals = parts[0], parts[1]
                                else:
                                    continue
                            keys = [k.strip() for k in vals.split(",") if k.strip()]
                            subclass_req_map[cls_key.strip()] = keys
        except FileNotFoundError:
            subclass_req_map = {}
        required = subclass_req_map.get(class_name, [])
        
        # Collect existing keys for dropdown
        existing_keys = []
        if os.path.exists(DATABASE_KEYS_FILE):
            with open(DATABASE_KEYS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(class_name + "_"):
                        existing_keys.append(line[len(class_name) + 1:])
        
        cur_props = obj.properties if isinstance(obj.properties, dict) else {}
        existing_keys = sorted(set(existing_keys) | set(cur_props.keys()))
        
        # Open PropertyEditor with pre-filled values
        _, custom_props = get_class_schema(class_name)
        if isinstance(existing_keys, list):
            existing_keys.extend(custom_props)
        else:
            existing_keys = set(existing_keys) | set(custom_props)
        editor = PropertyEditor(self.root, cls, set(existing_keys), required)
        try:
            for i, rp in enumerate(required):
                if i < len(editor.rows):
                    kv, vv, _cb = editor.rows[i]
                    kv.set(rp)
                    vv.set(cur_props.get(rp, ""))
        except Exception:
            pass
        
        try:
            opt_props = {k: v for k, v in cur_props.items() if k not in required}
            opt_index = len(required)
            for k, v in opt_props.items():
                if opt_index >= len(editor.rows):
                    editor.add_optional_row()
                kv, vv, _cb = editor.rows[opt_index]
                kv.set(k)
                vv.set(v)
                opt_index += 1
        except Exception:
            pass
        
        self.root.wait_window(editor)
        if editor.result is None:
            return
        
        # Create dialog to select destination parent (tree view showing all nodes, greyed-out invalid parents)
        class DestinationDialog(tk.Toplevel):
            def __init__(self, master, child_cls, single_tree=None, multi_trees=None):
                super().__init__(master)
                self.title("Select Destination Parent")
                self.geometry("450x500")
                self.result = None
                self.child_cls = child_cls
                self.single_tree = single_tree
                self.multi_trees = multi_trees or {}
                self.node_data = {}
                self.valid_nodes = set()
                self.ok_btn = None
                
                ttk.Label(self, text="Select destination parent:").pack(anchor="w", padx=10, pady=10)
                
                # Create treeview with scrollbar
                tree_frame = ttk.Frame(self)
                tree_frame.pack(fill="both", expand=True, padx=10, pady=5)
                
                self.treeview = ttk.Treeview(tree_frame)
                self.treeview.pack(side="left", fill="both", expand=True)
                
                scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.treeview.yview)
                scrollbar.pack(side="right", fill="y")
                self.treeview.configure(yscrollcommand=scrollbar.set)
                
                # Configure tags for valid/invalid nodes
                self.treeview.tag_configure("valid", foreground="black")
                self.treeview.tag_configure("invalid", foreground="gray60")
                
                # Populate tree based on mode
                if single_tree:
                    self._populate_tree_single(single_tree)
                else:
                    self._populate_tree_multi()
                
                # Bind selection event
                self.treeview.bind("<<TreeviewSelect>>", self._on_select)
                
                btn_frame = ttk.Frame(self)
                btn_frame.pack(pady=10)
                self.ok_btn = ttk.Button(btn_frame, text="OK", command=self.on_ok, state="disabled")
                self.ok_btn.pack(side="left", padx=5)
                ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side="left", padx=5)
            
            def _can_parent(self, node, tree):
                """Check if a node can accept the child class"""
                if node.tag == "SYSTEM":
                    return True
                obj = node.data.get("obj")
                if not obj:
                    return False
                try:
                    permitted = resolve_permitted_children(obj)
                    return self.child_cls.__name__ in permitted
                except Exception:
                    return False
            
            def _populate_tree_single(self, tree):
                """Populate tree for single-tree mode, showing all nodes with valid parents highlighted"""
                def add_node(node_id, parent_iid=""):
                    node = tree.get_node(node_id)
                    if not node:
                        return
                    
                    is_valid = self._can_parent(node, tree)
                    text = self._node_display_text(node)
                    iid = node.identifier
                    tags = ("valid",) if is_valid else ("invalid",)
                    
                    self.treeview.insert(parent_iid, "end", iid=iid, text=text, tags=tags)
                    self.node_data[iid] = (tree, node_id)
                    
                    if is_valid:
                        self.valid_nodes.add(iid)
                    
                    # Add children regardless of parent validity
                    for child in tree.children(node_id):
                        add_node(child.identifier, iid)
                
                add_node(tree.root)
            
            def _populate_tree_multi(self):
                """Populate tree for multi-tree mode, showing all nodes with valid parents highlighted"""
                for system_key in sorted(self.multi_trees.keys()):
                    info = self.multi_trees[system_key]
                    tree = info["tree"]
                    root_node = tree.get_node(tree.root)
                    
                    # Add system root
                    is_valid = self._can_parent(root_node, tree)
                    top_text = f"SYSTEM ({info.get('label', '')}) - {os.path.basename(info['file'])}"
                    top_iid = f"SYS::{system_key}"
                    tags = ("valid",) if is_valid else ("invalid",)
                    
                    self.treeview.insert("", "end", iid=top_iid, text=top_text, tags=tags)
                    self.node_data[top_iid] = (tree, tree.root, system_key)
                    
                    if is_valid:
                        self.valid_nodes.add(top_iid)
                    
                    # Add children recursively, regardless of parent validity
                    def add_node(node_id, parent_iid):
                        node = tree.get_node(node_id)
                        if not node:
                            return
                        
                        is_valid_node = self._can_parent(node, tree)
                        text = self._node_display_text(node)
                        iid = f"{top_iid}::{node_id}"
                        tags = ("valid",) if is_valid_node else ("invalid",)
                        
                        self.treeview.insert(parent_iid, "end", iid=iid, text=text, tags=tags)
                        self.node_data[iid] = (tree, node_id, system_key)
                        
                        if is_valid_node:
                            self.valid_nodes.add(iid)
                        
                        for child in tree.children(node_id):
                            add_node(child.identifier, iid)
                    
                    for child in tree.children(tree.root):
                        add_node(child.identifier, top_iid)
            
            def _node_display_text(self, node):
                """Get display text for a node"""
                if node.tag == "SYSTEM":
                    return f"SYSTEM ({node.data.get('Sample_System', '')})"
                else:
                    obj = node.data.get("obj")
                    if obj:
                        return f"{node.tag} [{obj.id}]"
                    return node.tag
            
            def _on_select(self, _event):
                """Enable/disable OK button based on selection validity"""
                sel = self.treeview.selection()
                if sel and sel[0] in self.valid_nodes:
                    self.ok_btn.config(state="normal")
                else:
                    self.ok_btn.config(state="disabled")
            
            def on_ok(self):
                sel = self.treeview.selection()
                if not sel:
                    messagebox.showwarning("Select", "Please select a destination.")
                    return
                
                iid = sel[0]
                if iid not in self.node_data or iid not in self.valid_nodes:
                    messagebox.showwarning("Select", "Selected parent is not valid for this node type.")
                    return
                
                self.result = self.node_data[iid]
                self.destroy()
            
            def on_cancel(self):
                self.destroy()
        
        # Create and show dialog
        if self.display_mode == "single":
            dest_dialog = DestinationDialog(self.root, cls, single_tree=self.tree_obj)
        else:
            dest_dialog = DestinationDialog(self.root, cls, multi_trees=self.multi_trees)
        self.root.wait_window(dest_dialog)
        
        if not dest_dialog.result:
            return
        
        dest_info = dest_dialog.result
        if len(dest_info) == 3:
            dest_tree, dest_parent_id, dest_system_key = dest_info
            dest_ctx_system_key = dest_system_key
        else:
            dest_tree, dest_parent_id = dest_info
            dest_ctx_system_key = ctx["system_key"]
        
        # Create new node with same class and properties
        new_props = editor.result
        try:
            new_obj = cls(**new_props)
            if hasattr(new_obj, "log_keys"):
                new_obj.log_keys()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create new instance: {e}")
            return
        
        dest_tree.create_node(tag=class_name,
                            identifier=new_obj.id,
                            parent=dest_parent_id,
                            data={"obj": new_obj})
        
        self._refresh_after_tree_change(system_key=dest_ctx_system_key, focus_node_id=new_obj.id)
        if self.display_mode == "multi":
            self.refresh_status(f"Copied {class_name} node.")
        else:
            self.refresh_status(f"Copied {class_name} node.")

    def _reset_multi_state(self):
        self.multi_trees = {}
        self.treeview_index = {}
        self.treeview_system_iids = {}


    def on_closing(self):
        if getattr(self, "last_action_was_save_archive_close", False) or (not self.tree_obj and not self.multi_trees):
            self.root.destroy()
            return
        answer = messagebox.askyesnocancel("Quit","'Yes' to save, archive and close, or 'No' to discard recent changes")
        if answer is True:  # Yes
            self._save_archive_and_close()
            self.root.destroy()
        elif answer is False:  # No
            self.root.destroy()
        else:  # Cancel
            pass

    def _clear_loaded_trees(self):
        self.tree_obj = None
        self.current_file = None
        self.display_mode = "single"
        self._reset_multi_state()
        self.treeview.delete(*self.treeview.get_children())
        self.parent_label_var.set("-")
        self.class_cb['values'] = []
        self.class_var.set("")
        self.rainbow_active = False

    def _selected_node_context(self):
        sel = self.treeview.selection()
        if not sel:
            return None
        tv_iid = sel[0]

        if self.display_mode == "single":
            if not self.tree_obj:
                return None
            node = self.tree_obj.get_node(tv_iid)
            if node is None:
                return None
            return {
                "tree": self.tree_obj,
                "node": node,
                "node_id": node.identifier,
                "system_key": "single",
                "file": self.current_file,
                "is_system_root": node.identifier == self.tree_obj.root,
                "tv_iid": tv_iid,
            }

        payload = self.treeview_index.get(tv_iid)
        if not payload:
            return None
        system_key = payload["system_key"]
        node_id = payload["node_id"]
        system_info = self.multi_trees.get(system_key)
        if not system_info:
            return None
        tree = system_info["tree"]
        node = tree.get_node(node_id)
        if node is None:
            return None
        return {
            "tree": tree,
            "node": node,
            "node_id": node_id,
            "system_key": system_key,
            "file": system_info["file"],
            "is_system_root": node_id == tree.root,
            "tv_iid": tv_iid,
        }

    def _multi_tree_iid(self, system_key, node_id):
        root_iid = self.treeview_system_iids.get(system_key)
        if not root_iid:
            return None
        tree = self.multi_trees[system_key]["tree"]
        if node_id == tree.root:
            return root_iid
        return f"{root_iid}::{node_id}"

    def _refresh_after_tree_change(self, system_key=None, focus_node_id=None):
        self.last_action_was_save_archive_close = False
        if self.display_mode == "multi":
            self.populate_multi_treeview(expand_system_key=system_key, focus_node_id=focus_node_id)
        else:
            self.populate_treeview()

    def _hide_discover_button(self):
        if self.discover_btn:
            try:
                self.discover_btn.pack_forget()
            except Exception:
                try:
                    self.discover_btn.destroy()
                except Exception:
                    pass
            self.discover_btn = None

    def create_new_tree(self):
        sample_system = simpledialog.askstring("Sample System", "Sample System:", parent=self.root)
        if not sample_system:
            return
            
        from database_GUI import TREE_STORAGE_DIR
        filename = filedialog.asksaveasfilename(
            initialdir=TREE_STORAGE_DIR,
            initialfile=f"{sample_system}.json",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            title="Save New Tree As",
            parent=self.root
        )
        if not filename:
            return
            
        import treelib
        import json
        self.display_mode = "single"
        self._reset_multi_state()
        self.tree_obj = treelib.Tree()
        root_id = "SYSTEM"
        self.sort_mode = "none"
        self.tree_obj.create_node(
            tag="SYSTEM", 
            identifier=root_id, 
            data={"Sample_System": sample_system, "sort_mode": "none"}
        )
        
        # Serialize and save immediately
        def serialize(t):
            nodes = []
            for node in t.all_nodes():
                if node.identifier == t.root:
                    continue
                nodes.append(node.data.get("obj").to_dict()) # obj doesn't exist for root
            return {"root": {"id": t.root, "sample_system": t.get_node(t.root).data.get("Sample_System")}, "nodes": nodes}
            
        with open(filename, "w", encoding="utf-8") as f:
            json.dump({"root": {"id": "SYSTEM", "sample_system": sample_system}, "nodes": []}, f, indent=2)
            
        self.current_file = filename
        self.sort_var.set("none")
        self._hide_discover_button()
        self.refresh_status(f"Created new tree: {os.path.basename(filename)}")
        try:
            with open(os.path.join(BASE_DIR, ".db_cache.json"), "w") as f:
                import json
                json.dump({"last_tree": filename}, f)
        except Exception:
            pass
        self._refresh_after_tree_change(focus_node_id=root_id)

    def load_tree(self):
        filename = filedialog.askopenfilename(
            initialdir=TREE_STORAGE_DIR,
            filetypes=[("JSON Files", "*.json")],
            title="Load Tree")
        if not filename:
            return
        self._load_specific_tree(filename)

    def _load_specific_tree(self, filename):
        try:
            self.display_mode = "single"
            self._reset_multi_state()
            self.tree_obj = deserialize_tree(filename)
            # Restore sort mode from tree
            root_node = self.tree_obj.get_node(self.tree_obj.root)
            if root_node:
                self.sort_mode = root_node.data.get("sort_mode", "none")
                self.sort_var.set(self.sort_mode)
            self.current_file = filename
            self.populate_treeview()
            # hide discover button once a tree is loaded
            self._hide_discover_button()
            self.refresh_status(f"Loaded {os.path.basename(filename)}")
            self.last_action_was_save_archive_close = False
            try:
                with open(os.path.join(BASE_DIR, ".db_cache.json"), "w") as f:
                    import json
                    json.dump({"last_tree": filename}, f)
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}")

    def load_all_trees(self):
        files = []
        try:
            for name in sorted(os.listdir(TREE_STORAGE_DIR)):
                path = os.path.join(TREE_STORAGE_DIR, name)
                if not os.path.isfile(path):
                    continue
                if not name.lower().endswith(".json"):
                    continue
                files.append(path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to scan databases folder: {e}")
            return

        if not files:
            messagebox.showwarning("No Trees", "No JSON tree files found in databases folder.")
            return

        loaded = {}
        failed = []
        for idx, path in enumerate(files):
            try:
                tree = deserialize_tree(path)
                system_node = tree.get_node(tree.root)
                system_name = ""
                sort_mode = "none"
                if system_node is not None:
                    system_name = system_node.data.get("Sample_System", "")
                    sort_mode = system_node.data.get("sort_mode", "none")
                key = f"{idx:04d}_{os.path.basename(path)}"
                loaded[key] = {
                    "tree": tree,
                    "file": path,
                    "label": f"{system_name}" if system_name else os.path.basename(path),
                }
                self.sort_state[key] = sort_mode
            except Exception as e:
                failed.append(f"{os.path.basename(path)}: {e}")

        if not loaded:
            messagebox.showerror("Error", "No trees could be loaded.")
            return

        self.display_mode = "multi"
        self.tree_obj = None
        self.current_file = None
        self.multi_trees = loaded
        # Set sort_var to "none" for multi-tree display
        self.sort_var.set("none")
        self.populate_multi_treeview()
        self.parent_label_var.set("-")
        self.class_cb['values'] = []
        self.class_var.set("")
        self._hide_discover_button()
        self.last_action_was_save_archive_close = False

        if failed:
            self.refresh_status(f"Loaded {len(loaded)} trees ({len(failed)} failed).")
        else:
            self.refresh_status(f"Loaded {len(loaded)} trees.")


    def load_multiple_trees(self):
        filenames = filedialog.askopenfilenames(
            initialdir=TREE_STORAGE_DIR,
            filetypes=[("JSON Files", "*.json")],
            title="Load Multiple Trees")
        if not filenames:
            return

        loaded = {}
        failed = []
        for idx, path in enumerate(filenames):
            try:
                tree = deserialize_tree(path)
                system_node = tree.get_node(tree.root)
                system_name = ""
                sort_mode = "none"
                if system_node is not None:
                    system_name = system_node.data.get("Sample_System", "")
                    sort_mode = system_node.data.get("sort_mode", "none")
                key = f"{idx:04d}_{os.path.basename(path)}"
                loaded[key] = {
                    "tree": tree,
                    "file": path,
                    "label": f"{system_name}" if system_name else os.path.basename(path),
                }
                self.sort_state[key] = sort_mode
            except Exception as e:
                failed.append(f"{os.path.basename(path)}: {e}")

        if not loaded:
            messagebox.showerror("Error", "No trees could be loaded.")
            return

        self.display_mode = "multi"
        self.tree_obj = None
        self.current_file = None
        self.multi_trees = loaded
        self.sort_var.set("none")
        self.populate_multi_treeview()
        self.parent_label_var.set("-")
        self.class_cb['values'] = []
        self.class_var.set("")
        self._hide_discover_button()
        self.last_action_was_save_archive_close = False

        if failed:
            self.refresh_status(f"Loaded {len(loaded)} trees ({len(failed)} failed).")
        else:
            self.refresh_status(f"Loaded {len(loaded)} trees.")

    def close_selected_tree(self):
        selected = self.treeview.selection()
        if not selected:
            messagebox.showwarning("Close Tree", "No node selected. Please select a node from the tree you want to close.")
            return

        if self.display_mode == "multi":
            iid = selected[0]
            while True:
                parent = self.treeview.parent(iid)
                if not parent:
                    break
                iid = parent
            
            system_key = None
            for key, top_iid in self.treeview_system_iids.items():
                if top_iid == iid:
                    system_key = key
                    break
            
            if not system_key:
                system_key = self.treeview_index.get(selected[0], {}).get("system_key")
            
            if not system_key or system_key not in self.multi_trees:
                messagebox.showwarning("Close Tree", "Could not identify the tree to close.")
                return
            
            info = self.multi_trees[system_key]
            ans = messagebox.askyesnocancel("Close Tree", f"Save and archive '{info.get('label', system_key)}' before closing?")
            if ans is None:
                return
            if ans:
                try:
                    tree = info["tree"]
                    filepath = info["file"]
                    sort_mode = self.sort_state.get(system_key, "none")
                    serialize_tree(tree, filepath, sort_mode=sort_mode)
                    
                    archive_dir = os.path.join(os.path.dirname(filepath) if filepath else TREE_STORAGE_DIR, "archive")
                    os.makedirs(archive_dir, exist_ok=True)
                    ts = datetime.now().strftime("%y%m%d")
                    base = os.path.basename(filepath)
                    root, ext = os.path.splitext(base)
                    out_name = f"{root}_{ts}{ext}" if ext else f"{root}_{ts}"
                    out_path = os.path.join(archive_dir, out_name)
                    serialize_tree(tree, out_path, sort_mode=sort_mode)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save/archive tree: {e}")
                    return

            del self.multi_trees[system_key]
            if system_key in self.sort_state:
                del self.sort_state[system_key]
            
            if not self.multi_trees:
                self.display_mode = "single"
                self.treeview.delete(*self.treeview.get_children())
            else:
                self.populate_multi_treeview()
            self.refresh_status("Closed tree.")

        else:
            if not self.tree_obj:
                return
            ans = messagebox.askyesnocancel("Close Tree", "Save and archive the current tree before closing?")
            if ans is None:
                return
            if ans:
                try:
                    self.save_tree()
                    filepath = self.current_file
                    if filepath:
                        archive_dir = os.path.join(os.path.dirname(filepath), "archive")
                        os.makedirs(archive_dir, exist_ok=True)
                        ts = datetime.now().strftime("%y%m%d")
                        base = os.path.basename(filepath)
                        root, ext = os.path.splitext(base)
                        out_name = f"{root}_{ts}{ext}" if ext else f"{root}_{ts}"
                        out_path = os.path.join(archive_dir, out_name)
                        root_node = self.tree_obj.get_node(self.tree_obj.root)
                        sort_mode = root_node.data.get("sort_mode", "none") if root_node else "none"
                        serialize_tree(self.tree_obj, out_path, sort_mode=sort_mode)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save/archive tree: {e}")
                    return

            self._clear_loaded_trees()
            self.refresh_status("Closed tree.")

    def collapse_all_trees(self):
        def _collapse(item):
            self.treeview.item(item, open=False)
            for child in self.treeview.get_children(item):
                _collapse(child)
        for item in self.treeview.get_children():
            _collapse(item)

    def expand_all_trees(self):
        def _expand(item):
            self.treeview.item(item, open=True)
            for child in self.treeview.get_children(item):
                _expand(child)
        for item in self.treeview.get_children():
            _expand(item)

    def import_legacy_keys(self):
        import json
        import os
        from database_classes import DATABASE_STRUCTURE_FILE
        
        legacy_dir = filedialog.askdirectory(title="Select Folder containing database_keys.txt and required_properties.txt")
        if not legacy_dir: return
        
        db_keys_file = os.path.join(legacy_dir, "database_keys.txt")
        req_props_file = os.path.join(legacy_dir, "required_properties.txt")
        
        if not os.path.exists(db_keys_file) and not os.path.exists(req_props_file):
            messagebox.showinfo("Info", "No legacy files found in that directory.")
            return
            
        try:
            with open(DATABASE_STRUCTURE_FILE, 'r', encoding='utf-8') as f:
                schema = json.load(f)
        except:
            schema = {}

        new_classes = set()

        if os.path.exists(req_props_file):
            with open(req_props_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or ':' not in line: continue
                    cls_name, props = line.split(':', 1)
                    cls_name = cls_name.strip()
                    keys = [k.strip() for k in props.split(',') if k.strip()]
                    
                    if cls_name not in schema:
                        schema[cls_name] = {"base": "Sample", "required": [], "custom": [], "permitted_children": []}
                        new_classes.add(cls_name)
                        
                    for k in keys:
                        if k not in schema[cls_name]["required"] and k not in schema[cls_name]["custom"]:
                            schema[cls_name]["required"].append(k)

        if os.path.exists(db_keys_file):
            with open(db_keys_file, 'r', encoding='utf-8') as f:
                # We need to sort known schema classes by length descending to match longest prefix
                known_classes = sorted(schema.keys(), key=len, reverse=True)
                for line in f:
                    line = line.strip()
                    if not line or '_' not in line: continue
                    
                    matched_cls = None
                    matched_prop = None
                    for k_cls in known_classes:
                        if line.startswith(k_cls + '_'):
                            matched_cls = k_cls
                            matched_prop = line[len(k_cls)+1:]
                            break
                            
                    if not matched_cls:
                        # Fallback to rsplit
                        matched_cls, matched_prop = line.rsplit('_', 1)
                        matched_cls = matched_cls.strip()
                        matched_prop = matched_prop.strip()
                        
                    if matched_cls not in schema:
                        schema[matched_cls] = {"base": "Sample", "required": [], "custom": [], "permitted_children": []}
                        new_classes.add(matched_cls)
                        
                    if matched_prop not in schema[matched_cls]["required"] and matched_prop not in schema[matched_cls]["custom"]:
                        schema[matched_cls]["custom"].append(matched_prop)

        with open(DATABASE_STRUCTURE_FILE, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=4)
            
        import database_classes
        if hasattr(database_classes, "create_class_from_schema"):
            for cls_name in new_classes:
                database_classes.create_class_from_schema(cls_name)

        if new_classes:
            msg = f"Legacy keys imported successfully.\n\nThe following unknown classes were found and defaulted to 'Sample' with no children:\n{', '.join(new_classes)}\n\nPlease update their base and permitted children in the JSON or Structure Browser."
            messagebox.showwarning("Import Complete", msg)
        else:
            messagebox.showinfo("Import Complete", "Legacy keys merged successfully into existing structure.")
            
    def save_tree(self):
        if self.display_mode == "multi":
            if not self.multi_trees:
                messagebox.showwarning("Save", "No trees to save.")
                return
            saved = 0
            for system_key, info in self.multi_trees.items():
                sort_mode = self.sort_state.get(system_key, "none")
                serialize_tree(info["tree"], info["file"], sort_mode=sort_mode)
                saved += 1
            self.refresh_status(f"Saved {saved} trees.")
            return

        if not self.tree_obj:
            return
        if not self.current_file:
            self.current_file = filedialog.asksaveasfilename(
                initialdir=TREE_STORAGE_DIR,
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")])
            if not self.current_file:
                return
        serialize_tree(self.tree_obj, self.current_file, sort_mode=self.sort_mode)
        self.refresh_status("Tree saved.")
    
    # ---------- Discover Required Properties Option (integrated into GUI) ----------
    def _on_discover_click(self):
        try:
            discover_required_properties()
        except NameError:
            messagebox.showerror("Error", "discover_required_properties is not available.")
            return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to discover required properties: {e}")
            return
        try:
            self.discover_btn.destroy()
        except Exception:
            pass
        self.discover_btn = None
        messagebox.showinfo("Discovered", "Required properties were successfully discovered.")

    
    # ---------- Build the treeview display ----------
    def rainbow_colours(self):
        return [
            "#000000",  # black
            "#FF0000",  # red
            "#FF7F00",  # orange
            "#00FF00",  # green
            "#00FFFF",  # cyan
            "#0000FF",  # blue
            "#7F00FF",  # violet
            "#FFBF00",  # amber
            "#BFFF00",  # chartreuse
            "#00FF80",  # aquamarine
            "#0080FF",  # azure
            "#4000FF",  # indigo
            "#FF00BF",  # rose
        ]

    def _activate_rainbow_mode(self, colours):
        try:
            self.treeview.delete(*self.treeview.get_children())
            if not self.tree_obj:
                return

            def add(node_id, depth=0):
                node = self.tree_obj.get_node(node_id)
                if node is None:
                    return
                parent = self.tree_obj.parent(node_id)
                parent_tv = "" if parent is None else parent.identifier
                text = self.node_text(node)
                color_tag = f"rainbow_{depth % len(colours)}"
                try:
                    self.treeview.tag_configure(color_tag, foreground=colours[depth % len(colours)])
                except Exception:
                    pass
                self.treeview.insert(parent_tv, "end", iid=node.identifier, text=text, tags=(color_tag,))
                try:
                    self.treeview.item(node.identifier, open=True)
                except Exception:
                    pass
                for child in self.get_sort_children(self.tree_obj, node_id):
                    add(child.identifier, depth + 1)

            add(self.tree_obj.root)
            for iid in self.treeview.get_children():
                try:
                    self.treeview.item(iid, open=True)
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Error", f"Failed to activate rainbow mode: {e}")

    def toggle_rainbow_mode(self):
        has_single = self.display_mode == "single" and self.tree_obj is not None
        has_multi = self.display_mode == "multi" and bool(self.multi_trees)
        if not (has_single or has_multi):
            messagebox.showinfo("Rainbow Mode", "Load a tree first.")
            return
        if not self.rainbow_active:
            self.rainbow_active = True
            if self.display_mode == "single":
                self._activate_rainbow_mode(self.rainbow_colours())
            else:
                self.populate_multi_treeview()
        else:
            self.rainbow_active = False
            if self.display_mode == "single":
                self.populate_treeview()
            else:
                self.populate_multi_treeview()

    def populate_treeview(self):
        self.treeview_index = {}
        self.treeview_system_iids = {}

        if self.display_mode == "multi":
            self.populate_multi_treeview()
            return

        if self.rainbow_active:
            self._activate_rainbow_mode(self.rainbow_colours())
            return

        self.treeview.delete(*self.treeview.get_children())
        if not self.tree_obj:
            return


        # configure tag for system children (red text)
        try:
            self.treeview.tag_configure("system_child", foreground="red")
        except Exception:
            # Some environments may not support tag_configure; ignore safely
            pass

        # Build hierarchical insertion and expand every node as it's added
        def add(node_id):
            node = self.tree_obj.get_node(node_id)
            parent = self.tree_obj.parent(node_id)
            parent_tv = "" if parent is None else parent.identifier
            text = self.node_text(node)

            # mark direct children of the SYSTEM root with the "system_child" tag
            tags = ()
            try:
                if parent is not None and parent.identifier == self.tree_obj.root and node.tag != "SYSTEM":
                    tags = ("system_child",)
            except Exception:
                tags = ()

            self.treeview.insert(parent_tv, "end", iid=node.identifier, text=text, tags=tags)
            # ensure the inserted node is expanded
            try:
                self.treeview.item(node.identifier, open=True)
            except Exception:
                pass
            for child in self.get_sort_children(self.tree_obj, node_id):
                add(child.identifier)
        add(self.tree_obj.root)
        # make sure top-level items are expanded as well
        try:
            for iid in self.treeview.get_children():
                self.treeview.item(iid, open=True)
        except Exception:
            pass

    def populate_multi_treeview(self, expand_system_key=None, focus_node_id=None):
        self.treeview.delete(*self.treeview.get_children())
        self.treeview_index = {}
        self.treeview_system_iids = {}

        if not self.multi_trees:
            return


        def add_node(system_key, tree, node_id, parent_iid, expand_this_system, depth):
            node = tree.get_node(node_id)
            node_iid = self._multi_tree_iid(system_key, node_id)
            if node_iid is None or node is None:
                return
            tags = ()
            if self.rainbow_active:
                color_tag = f"rainbow_{depth % len(self.rainbow_colours())}"
                tags = (color_tag,)
                try:
                    self.treeview.tag_configure(color_tag, foreground=self.rainbow_colours()[depth % len(self.rainbow_colours())])
                except Exception:
                    pass
            else:
                parent = tree.parent(node_id)
                try:
                    if parent is not None and parent.identifier == tree.root and node.tag != "SYSTEM":
                        tags = ("system_child",)
                except Exception:
                    tags = ()
            self.treeview.insert(parent_iid, "end", iid=node_iid, text=self.node_text(node), tags=tags)
            self.treeview.item(node_iid, open=bool(expand_this_system))
            self.treeview_index[node_iid] = {"system_key": system_key, "node_id": node_id}
            for child in self.get_sort_children(tree, node_id):
                add_node(system_key, tree, child.identifier, node_iid, expand_this_system, depth + 1)

        try:
            self.treeview.tag_configure("system_child", foreground="red")
        except Exception:
            pass

        for system_key in sorted(self.multi_trees.keys()):
            info = self.multi_trees[system_key]
            tree = info["tree"]
            root_node = tree.get_node(tree.root)
            top_iid = f"SYS::{system_key}"
            self.treeview_system_iids[system_key] = top_iid
            label = info.get("label", "")
            display_name = label if label else "SYSTEM"
            top_text = f"{self.node_text(root_node)} - {os.path.basename(info['file'])}"
            if root_node and root_node.data.get("Sample_System"):
                top_text = f"SYSTEM ({display_name}) - {os.path.basename(info['file'])}"
            top_tags = ()
            if self.rainbow_active:
                top_color_tag = "rainbow_0"
                top_tags = (top_color_tag,)
                try:
                    self.treeview.tag_configure(top_color_tag, foreground=self.rainbow_colours()[0])
                except Exception:
                    pass
            self.treeview.insert("", "end", iid=top_iid, text=top_text, tags=top_tags)
            self.treeview.item(top_iid, open=bool(expand_system_key and expand_system_key == system_key))
            self.treeview_index[top_iid] = {"system_key": system_key, "node_id": tree.root}
            for child in self.get_sort_children(tree, tree.root):
                add_node(system_key, tree, child.identifier, top_iid, expand_system_key and expand_system_key == system_key, 1)

        if focus_node_id and expand_system_key:
            tree = self.multi_trees.get(expand_system_key, {}).get("tree")
            if tree:
                path = []
                cur = focus_node_id
                while True:
                    node = tree.get_node(cur)
                    if node is None:
                        break
                    path.append(cur)
                    parent = tree.parent(cur)
                    if parent is None:
                        break
                    cur = parent.identifier
                for nid in reversed(path):
                    iid = self._multi_tree_iid(expand_system_key, nid)
                    if iid:
                        try:
                            self.treeview.item(iid, open=True)
                        except Exception:
                            pass
                target_iid = self._multi_tree_iid(expand_system_key, focus_node_id)
                if target_iid:
                    try:
                        self.treeview.selection_set(target_iid)
                        self.treeview.see(target_iid)
                    except Exception:
                        pass

    def _expand_all_descendants(self, iid):
        for child in self.treeview.get_children(iid):
            try:
                self.treeview.item(child, open=True)
            except Exception:
                pass
            self._expand_all_descendants(child)

    def on_treeview_open(self, _event):
        if self.display_mode != "multi":
            return
        sel = self.treeview.selection()
        opened_iid = sel[0] if sel else self.treeview.focus()
        if not opened_iid:
            return
        payload = self.treeview_index.get(opened_iid)
        if not payload:
            return
        system_key = payload.get("system_key")
        node_id = payload.get("node_id")
        info = self.multi_trees.get(system_key)
        if not info:
            return
        tree = info.get("tree")
        if not tree:
            return
        if node_id == tree.root:
            self._expand_all_descendants(opened_iid)

    def node_text(self, node):
        ''' Generate display text for a tree node '''
        tag = node.tag
        if tag == "SYSTEM":
            text = f"SYSTEM ({node.data.get('Sample_System')})"
        else:
            obj = node.data["obj"]
            text = f"{tag} [{obj.id}]"
            # append material and/or name properties if present
            # Find material
            mat = None
            try:
                props = getattr(obj, "properties", {})
                if isinstance(props, dict):
                    mat = props.get("material")
                if mat is None:
                    mat = getattr(obj, "material", None)
            except Exception:
                mat = None
            if mat not in (None, ""):
                mattext = f": {mat}"
            else:
                mattext = ""
            # Find name
            nam = None
            try:
                props = getattr(obj, "properties", {})
                if isinstance(props, dict):
                    nam = props.get("name")
                if nam is None:
                    nam = getattr(obj, "name", None)
            except Exception:
                nam = None
            if nam not in (None, ""):
                namtext = f" ({nam})"
            else:
                namtext = ""
            # If material indicates a calibration chip, show raw sputter values (no added units)
            caltext = ""
            try:
                props = getattr(obj, "properties", {})
                if isinstance(props, dict) and mat and "calibration" in str(mat).lower():
                    sc = props.get("sputter_current")
                    sf = props.get("sputter_flow")
                    parts = []
                    if sc not in (None, ""):
                        parts.append(str(sc))
                    if sf not in (None, ""):
                        parts.append(str(sf))
                    if parts:
                        caltext = f" ({', '.join(parts)})"
            except Exception:
                caltext = ""
            text += mattext + caltext + namtext
            # Special cases for certain classes to append extra info
            try:
                if obj.__class__.__name__ == "Annealing":
                    props = getattr(obj, "properties", {})
                    if isinstance(props, dict):
                        temp = props.get("temperature_C")
                        if temp not in (None, ""):
                            text += f" ({temp}\u00B0C)"
                if obj.__class__.__name__ == "Micromechanical_testing":
                    props = getattr(obj, "properties", {})
                    if isinstance(props, dict):
                        test_type = props.get("test_type")
                        if test_type not in (None, ""):
                            text += f" ({test_type})"
                if obj.__class__.__name__ == "XRay_analysis":
                    props = getattr(obj, "properties", {})
                    if isinstance(props, dict):
                        mode = props.get("mode")
                        if mode not in (None, ""):
                            text += f" ({mode})"
            except Exception:
                pass
        return text
    
    def edit_node(self):
        ctx = self._selected_node_context()
        if not ctx:
            messagebox.showwarning("Select", "Select a node first.")
            return
        node_id = ctx["node_id"]
        node = ctx["node"]
        tree_obj = ctx["tree"]
        if node is None:
            messagebox.showerror("Error", "Selected node not found in tree.")
            return
        if node.tag == "SYSTEM":
            messagebox.showwarning("Edit", "SYSTEM node cannot be edited.")
            return

        obj = node.data.get("obj")
        if obj is None:
            messagebox.showerror("Error", "No object data for selected node.")
            return

        class_name = node.tag
        cls = obj.__class__

        # Load required properties (reuse same logic as add_child_node)
        try:
            with open(REQUIRED_PROPERTIES_FILE, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if not txt:
                subclass_req_map = {}
            else:
                try:
                    subclass_req_map = json.loads(txt)
                    if not isinstance(subclass_req_map, dict):
                        raise ValueError("JSON root is not an object")
                except Exception:
                    subclass_req_map = {}
                    for line in txt.splitlines():
                        line = line.split("#", 1)[0].strip()
                        if not line:
                            continue
                        if ":" in line:
                            cls_key, vals = line.split(":", 1)
                        elif "=" in line:
                            cls_key, vals = line.split("=", 1)
                        else:
                            parts = line.split()
                            if len(parts) == 2:
                                cls_key, vals = parts[0], parts[1]
                            else:
                                continue
                        keys = [k.strip() for k in vals.split(",") if k.strip()]
                        subclass_req_map[cls_key.strip()] = keys
        except FileNotFoundError:
            subclass_req_map = {}
        required = subclass_req_map.get(class_name, [])

        # Collect existing keys for dropdown (filter by prefix type_)
        existing_keys = []
        if os.path.exists(DATABASE_KEYS_FILE):
            with open(DATABASE_KEYS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(class_name + "_"):
                        existing_keys.append(line[len(class_name) + 1:])

        # Include current object's property keys as available existing keys
        cur_props = obj.properties if isinstance(obj.properties, dict) else {}
        existing_keys = sorted(set(existing_keys) | set(cur_props.keys()))

        _, custom_props = get_class_schema(class_name)
        if isinstance(existing_keys, list):
            existing_keys.extend(custom_props)
        else:
            existing_keys = set(existing_keys) | set(custom_props)
        editor = PropertyEditor(self.root, cls, set(existing_keys), required)

        # Prefill required rows with current values
        try:
            for i, rp in enumerate(required):
                if i < len(editor.rows):
                    kv, vv, _cb = editor.rows[i]
                    kv.set(rp)
                    vv.set(cur_props.get(rp, ""))
        except Exception:
            pass

        # Prefill optional rows with remaining current properties
        try:
            opt_props = {k: v for k, v in cur_props.items() if k not in required}
            opt_index = len(required)
            for k, v in opt_props.items():
                if opt_index >= len(editor.rows):
                    editor.add_optional_row()
                kv, vv, _cb = editor.rows[opt_index]
                kv.set(k)
                vv.set(v)
                opt_index += 1
        except Exception:
            pass

        self.root.wait_window(editor)
        if editor.result is None:
            return

        new_props = editor.result
        try:
            # Try to set properties back onto object
            if hasattr(obj, "properties"):
                obj.properties = new_props
            else:
                setattr(obj, "properties", new_props)
            if hasattr(obj, "log_keys"):
                obj.log_keys()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update properties: {e}")
            return

        # Refresh treeview to reflect any changes
        self._refresh_after_tree_change(system_key=ctx["system_key"], focus_node_id=node_id)
        if self.display_mode == "multi":
            self.refresh_status(f"Edited {class_name} node in {os.path.basename(ctx['file'])}.")
        else:
            self.refresh_status(f"Edited {class_name} node.")

    def _save_archive_and_close(self):
        self.save_tree()
        if self.display_mode == "multi":
            if not self.multi_trees:
                messagebox.showwarning("Save", "No trees to save.")
                return
            archive_dir = os.path.join(os.path.dirname(self.current_file) if self.current_file else TREE_STORAGE_DIR, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            ts = datetime.now().strftime("%y%m%d")
            try:
                for system_key, info in self.multi_trees.items():
                    sort_mode = self.sort_state.get(system_key, "none")
                    base = os.path.basename(info["file"])
                    root, ext = os.path.splitext(base)
                    out_name = f"{root}_{ts}{ext}" if ext else f"{root}_{ts}"
                    out_path = os.path.join(archive_dir, out_name)
                    serialize_tree(info["tree"], out_path, sort_mode=sort_mode)
                self._clear_loaded_trees()
                self.refresh_status("Ready")
                self.last_action_was_save_archive_close = True
            except Exception as e:
                messagebox.showerror("Error", f"Failed to archive trees: {e}")
            return

        if not self.tree_obj:
            messagebox.showwarning("Save", "No tree to save.")
            return
        base_file = self.current_file
        if not base_file:
            base_file = filedialog.asksaveasfilename(
                initialdir=TREE_STORAGE_DIR,
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")],
                title="Save Tree As")
        if not base_file:
            return
        # Ensure archive subdirectory exists
        archive_dir = os.path.join(os.path.dirname(self.current_file) if self.current_file else TREE_STORAGE_DIR, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        # Add timestamp to filename and move to archive
        base_file = os.path.join(archive_dir, os.path.basename(base_file))
        ts = datetime.now().strftime("%y%m%d")
        root, ext = os.path.splitext(base_file)
        new_filename = f"{root}_{ts}{ext}" if ext else f"{root}_{ts}"
        try:
            serialize_tree(self.tree_obj, new_filename, sort_mode=self.sort_mode)
            self.refresh_status(f"Saved as {os.path.basename(new_filename)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")
            return
        try:
            self._clear_loaded_trees()
            self.refresh_status("Ready")
            self.last_action_was_save_archive_close = True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to close tree: {e}")

    def on_select(self, _event):
        ctx = self._selected_node_context()
        if not ctx:
            self.parent_label_var.set("-")
            self.class_cb['values'] = []
            self.class_var.set("")
            self.update_properties_panel(None)
            return
        parent_label = ctx["node_id"]
        if self.display_mode == "multi":
            parent_label = f"{os.path.basename(ctx['file'])}: {ctx['node_id']}"
        self.parent_label_var.set(parent_label)
        self.populate_child_class_options(ctx["tree"], ctx["node_id"])
        self.update_properties_panel(ctx)

    def populate_child_class_options(self, tree_obj, parent_id):
        if parent_id == tree_obj.root:
            # root can have any top-level sample
            classes = sorted(get_sample_classes().keys())
        else:
            parent_node = tree_obj.get_node(parent_id)
            parent_obj = parent_node.data["obj"]
            permitted = resolve_permitted_children(parent_obj)
            # Convert to only those classes actually available
            classes_available = get_sample_classes()
            classes = [c for c in permitted if c in classes_available]
        self.class_cb['values'] = classes
        if classes:
            self.class_var.set(classes[0])
        else:
            self.class_var.set("")

    def add_child_node(self):
        ctx = self._selected_node_context()
        if not ctx:
            messagebox.showwarning("Select", "Select a parent node first.")
            return
        tree_obj = ctx["tree"]
        parent_id = ctx["node_id"]
        class_name = self.class_var.get()
        if not class_name:
            messagebox.showwarning("Class", "No child class available.")
            return
        classes = get_sample_classes()
        cls = classes[class_name]

        # Do not allow Processing_Step (or its subclasses) as direct children of the top Sample System node
        try:
            root_id = tree_obj.root
            base_ps = globals().get("Processing_Step")
            # fallback: search globals for a class named "Processing_Step"
            if base_ps is None:
                for obj in globals().values():
                    if inspect.isclass(obj) and obj.__name__ == "Processing_Step":
                        base_ps = obj
                        break
            if parent_id == root_id and base_ps and inspect.isclass(base_ps) and issubclass(cls, base_ps):
                messagebox.showwarning("Not allowed", "Processing Steps cannot be direct children of the top Sample System node.")
                return
        except Exception:
            pass

        # Determine required properties from file
        try:
            with open(REQUIRED_PROPERTIES_FILE, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                if not txt:
                    subclass_req_map = {}
                else:
                    try:
                        subclass_req_map = json.loads(txt)
                        if not isinstance(subclass_req_map, dict):
                            raise ValueError("JSON root is not an object")
                    except Exception:
                        subclass_req_map = {}
                        for line in txt.splitlines():
                            line = line.split("#", 1)[0].strip()  # allow comments with #
                            if not line:
                                continue
                            if ":" in line:
                                cls_key, vals = line.split(":", 1)
                            elif "=" in line:
                                cls_key, vals = line.split("=", 1)
                            else:
                                # single-class single-key line
                                parts = line.split()
                                if len(parts) == 2:
                                    cls_key, vals = parts[0], parts[1]
                                else:
                                    continue
                            keys = [k.strip() for k in vals.split(",") if k.strip()]
                            subclass_req_map[cls_key.strip()] = keys
        except FileNotFoundError:
            subclass_req_map = {}
        required = subclass_req_map.get(class_name, [])

        # Collect existing keys for dropdown (filter by prefix type_)
        existing_keys = []
        if os.path.exists(DATABASE_KEYS_FILE):
            with open(DATABASE_KEYS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(class_name + "_"):
                        existing_keys.append(line[len(class_name) + 1:])

        _, custom_props = get_class_schema(class_name)
        if isinstance(existing_keys, list):
            existing_keys.extend(custom_props)
        else:
            existing_keys = set(existing_keys) | set(custom_props)
        editor = PropertyEditor(self.root, cls, set(existing_keys), required)
        self.root.wait_window(editor)
        if editor.result is None:
            return
        kwargs = editor.result
        try:
            obj = cls(**kwargs)
            if hasattr(obj, "log_keys"):
                obj.log_keys()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create instance: {e}")
            return
        tree_obj.create_node(tag=class_name,
                      identifier=obj.id,
                      parent=parent_id,
                      data={"obj": obj})
        self._refresh_after_tree_change(system_key=ctx["system_key"], focus_node_id=obj.id)
        if self.display_mode == "multi":
            self.refresh_status(f"Added {class_name} node in {os.path.basename(ctx['file'])}.")
        else:
            self.refresh_status(f"Added {class_name} node.")

    # ---------- Search Property Window ----------
    def search_property(self):
        gui = self
        # Pop-up window for property search
        class SearchWindow(tk.Toplevel):
            def __init__(self, master, treeview):
                super().__init__(master)
                self.title("Search Property")
                self.geometry("460x360")
                self.resizable(False, False)
                self.treeview = treeview
                self.result_entries = []
                self.result_texts = []

                if gui.display_mode == "multi":
                    self.search_sources = [
                        (system_key, gui.multi_trees[system_key]["tree"]) for system_key in sorted(gui.multi_trees.keys())
                    ]
                else:
                    self.search_sources = [("single", gui.tree_obj)] if gui.tree_obj else []

                frame = ttk.Frame(self)
                frame.pack(fill="both", expand=True, padx=10, pady=10)

                # Load node types
                nodes_types = ['any']
                if os.path.exists(REQUIRED_PROPERTIES_FILE):
                    with open(REQUIRED_PROPERTIES_FILE, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.split('#', 1)[0].strip()
                            if not line:
                                continue
                            try:
                                cls_key = line.split(':', 1)[0].strip()
                                if cls_key not in nodes_types:
                                    nodes_types.append(cls_key)
                            except Exception:
                                continue
                    for r in ['Processing_Step', 'Sample']:
                        if r in nodes_types:
                            nodes_types.remove(r)
                nodes_types = sorted(nodes_types)
                        

                # Load database_keys
                db_keys = []
                self.additional_keys = ['id', 'entry_created_date']
                if os.path.exists(DATABASE_KEYS_FILE):
                    with open(DATABASE_KEYS_FILE, "r") as f:
                        db_keys = self.additional_keys + sorted(set(line.strip() for line in f if line.strip()))
                # Add 'id' and 'entry_created_date' as searchable property keys
                db_keys.append("<Custom key...>")
                
                def update_db_display_keys():
                    db_display_keys = self.additional_keys.copy()
                    if self.type_var.get() == "any":    # Show all keys
                        for node in nodes_types:
                            # if node == "any":
                            #     continue
                            prefix = node + "_"
                            for key in db_keys:
                                if key.startswith(prefix):
                                    trimmed_key = key[len(prefix):]
                                    if trimmed_key not in db_display_keys:
                                        db_display_keys.append(trimmed_key)
                    else:                           # Filter by selected node type
                        prefix = self.type_var.get() + "_"
                        for key in db_keys:
                            if key.startswith(prefix):
                                trimmed_key = key[len(prefix):]
                                if trimmed_key not in db_display_keys:
                                    db_display_keys.append(trimmed_key)
                    db_display_keys.append("<Custom key...>")
                    return db_display_keys
                    

                ttk.Label(frame, text="Node type:").grid(row=0, column=0, sticky="w")
                self.type_var = tk.StringVar(value='any')
                self.type_cb = ttk.Combobox(frame, values=nodes_types, textvariable=self.type_var, state="readonly", width=28)
                self.type_cb.grid(row=0, column=1, sticky="w")

                ttk.Label(frame, text="Property key:").grid(row=1, column=0, sticky="w")
                self.key_var = tk.StringVar()
                self.key_cb = ttk.Combobox(frame, values=update_db_display_keys(), textvariable=self.key_var, state="readonly", width=28)
                self.key_cb.grid(row=1, column=1, sticky="w")

                ttk.Label(frame, text="Property value (optional):").grid(row=2, column=0, sticky="w")
                self.val_var = tk.StringVar()
                self.val_entry = ttk.Entry(frame, textvariable=self.val_var, width=28)
                self.val_entry.grid(row=2, column=1, sticky="w")

                self.partial_var = tk.BooleanVar(value=False)
                self.partial_cb = ttk.Checkbutton(frame, text="Search partial match", variable=self.partial_var)
                self.partial_cb.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

                def on_key_select(_event):
                    # Handle custom key entry
                    if self.key_var.get() == "<Custom key...>":
                        new_key = simpledialog.askstring("Custom key", "Enter custom property key:", parent=self)
                        if new_key:
                            self.key_var.set(new_key)
                            if new_key not in db_keys and new_key != "":
                                db_keys.insert(-1, self.type_var.get() + "_" + new_key)
                                self.key_cb['values'] = update_db_display_keys()
                    # If node type changes, update available keys
                    elif _event.widget == self.type_cb:
                        self.key_cb['values'] = update_db_display_keys()
                        if self.key_cb.get() not in self.key_cb['values']:
                            self.key_var.set("")

                self.type_cb.bind("<<ComboboxSelected>>", on_key_select)
                self.key_cb.bind("<<ComboboxSelected>>", on_key_select)

                self.search_btn = ttk.Button(frame, text="Search", command=self.do_search)
                self.search_btn.grid(row=4, column=0, columnspan=2, pady=8)

                self.results_list = tk.Listbox(frame, height=10, width=48)
                self.results_list.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=4)
                self.results_list.bind("<Double-Button-1>", self.on_open_node)

                ttk.Button(frame, text="Close", command=self.destroy).grid(row=6, column=0, columnspan=2, pady=4)

                frame.rowconfigure(5, weight=1)
                frame.columnconfigure(1, weight=1)

            def _normalize_text(self, text, id_mode=False):
                import re

                normalized = str(text).lower()
                normalized = re.sub(r"[_-]+", " ", normalized)
                normalized = re.sub(r"\s+", " ", normalized).strip()
                if id_mode:
                    normalized = normalized.translate(str.maketrans({"b": "6", "6": "6", "e": "c", "c": "c"}))
                return normalized

            def _subsequence_match(self, query, target):
                query = query.replace(" ", "")
                target = target.replace(" ", "")
                if not query:
                    return True
                target_iter = iter(target)
                return all(ch in target_iter for ch in query)

            def _text_matches(self, query, target, mode, id_mode=False):
                if mode == "exact":
                    return self._normalize_text(query, id_mode) == self._normalize_text(target, id_mode)

                query_norm = self._normalize_text(query, id_mode)
                target_norm = self._normalize_text(target, id_mode)
                if query_norm in target_norm:
                    return True

                if mode == "subsequence":
                    return self._subsequence_match(query_norm, target_norm)

                return False

            def _matches_node(self, node, key_query, value_query, mode):
                obj = node.data.get("obj")
                if not obj:
                    return False, None, None

                props_raw = getattr(obj, "properties", {})
                if not isinstance(props_raw, dict):
                    return False, None, None

                props = dict(props_raw)
                props.update({attr_key: getattr(obj, attr_key, "") for attr_key in self.additional_keys if hasattr(obj, attr_key)})

                for prop_key, prop_value in props.items():
                    prop_key_text = str(prop_key)
                    prop_value_text = "" if prop_value is None else str(prop_value)
                    id_mode = prop_key_text.lower() == "id"

                    if mode == "exact":
                        key_ok = self._text_matches(key_query, prop_key_text, "exact", id_mode=False)
                        value_ok = True if value_query == "" else self._text_matches(value_query, prop_value_text, "exact", id_mode=id_mode)
                    else:
                        key_ok = self._text_matches(key_query, prop_key_text, mode, id_mode=False)
                        value_ok = True if value_query == "" else self._text_matches(value_query, prop_value_text, mode, id_mode=id_mode)

                    if key_ok and value_ok:
                        return True, prop_key_text, prop_value_text

                return False, None, None

            def _collect_matches(self, mode):
                matches = []
                seen_nodes = set()
                key_query = self.key_var.get().strip()
                value_query = self.val_var.get().strip()

                if not key_query:
                    return matches

                for system_key, tree in self.search_sources:
                    if not tree:
                        continue
                    for node in tree.all_nodes_itr():
                        if node.tag == "SYSTEM":
                            continue
                        if self.type_var.get() != "any" and node.tag != self.type_var.get():
                            continue

                        matched, prop_key, prop_value = self._matches_node(node, key_query, value_query, mode)
                        node_token = (system_key, node.identifier)
                        if matched and node_token not in seen_nodes:
                            matches.append((
                                system_key,
                                node.identifier,
                                node.tag,
                                getattr(node.data.get("obj"), "id", node.identifier),
                                prop_key,
                                prop_value,
                            ))
                            seen_nodes.add(node_token)

                return matches
                
            def do_search(self):
                self.results_list.delete(0, tk.END)
                self.search_btn.config(state='disabled')
                self.result_entries = []
                self.result_texts = []
                self.search_complete = False
                self.timeout_shown = False
                self.animate_frame = 0
                
                def animate_search_status():
                    if self.search_complete:
                        return
                    dots = [".", "..", "..."][self.animate_frame % 3]
                    self.search_btn.config(text=f"Searching{dots}")
                    self.animate_frame += 1
                    if not self.search_complete:
                        self.after(1000, animate_search_status)
                
                def check_timeout():
                    if self.search_complete:
                        return
                    if not self.timeout_shown:
                        self.timeout_shown = True
                        messagebox.showwarning("Search Timeout", "Search is taking longer than expected (10+ seconds)...", parent=self)
                    if not self.search_complete:
                        self.after(10000, check_timeout)
                
                def search_thread():
                    try:
                        if not self.search_sources:
                            return

                        key = self.key_var.get().strip()
                        if not key:
                            return

                        node_type = self.type_var.get()
                        if not self.partial_var.get():
                            matches = self._collect_matches("exact")
                        else:
                            exact_matches = self._collect_matches("exact")
                            seen_nodes = {(system_key, node_id) for system_key, node_id, *_rest in exact_matches}
                            normalized_matches = self._collect_matches("substring")
                            matches = exact_matches[:]
                            for item in normalized_matches:
                                if (item[0], item[1]) not in seen_nodes:
                                    matches.append(item)
                                    seen_nodes.add((item[0], item[1]))

                            if not matches:
                                # For non-contiguous, we need to use after_idle to show dialog
                                def ask_subsequence():
                                    try_non_contiguous = messagebox.askyesno(
                                        "No matches",
                                        "No exact or normalized partial matches were found. Search for non-contiguous character matches?",
                                        parent=self,
                                    )
                                    if try_non_contiguous:
                                        nonlocal matches
                                        matches = self._collect_matches("subsequence")
                                    update_results()
                                
                                self.after_idle(ask_subsequence)
                                return

                        update_results(matches)
                    except Exception as e:
                        self.after_idle(lambda: messagebox.showerror("Error", f"Search error: {e}", parent=self))
                
                def update_results(matches=None):
                    if matches is None:
                        return
                    
                    self.result_entries = [
                        {"system_key": system_key, "node_id": node_id}
                        for system_key, node_id, *_rest in matches
                    ]
                    if gui.display_mode == "multi":
                        self.result_texts = [
                            f"{node_tag} [{node_id_text}] ({os.path.basename(gui.multi_trees[system_key]['file'])}): {prop_key} = {prop_value}"
                            for system_key, node_id, node_tag, node_id_text, prop_key, prop_value in matches
                        ]
                    else:
                        self.result_texts = [
                            f"{node_tag} [{node_id_text}]: {prop_key} = {prop_value}"
                            for system_key, node_id, node_tag, node_id_text, prop_key, prop_value in matches
                        ]

                    self.results_list.delete(0, tk.END)
                    for text in self.result_texts:
                        self.results_list.insert(tk.END, text)

                    if not self.result_entries:
                        self.results_list.insert(tk.END, "No matches found.")
                    
                    self.search_complete = True
                    self.search_btn.config(text="Search", state='normal')
                
                # Start animation and timeout checks
                self.after(100, animate_search_status)
                self.after(10000, check_timeout)
                
                # Start search in background thread
                search_thread_obj = threading.Thread(target=search_thread, daemon=True)
                search_thread_obj.start()

            def on_open_node(self, _event):
                sel = self.results_list.curselection()
                if not sel:
                    return
                idx = sel[0]
                if idx >= len(self.result_entries):
                    return
                entry = self.result_entries[idx]
                # Open this node in the treeview, closing all others
                self.open_and_focus_node(entry["system_key"], entry["node_id"])
                self.destroy()

            def open_and_focus_node(self, system_key, node_id):
                # Collapse all nodes
                for iid in self.treeview.get_children(""):
                    self.recursive_close(iid)
                if gui.display_mode == "multi":
                    info = gui.multi_trees.get(system_key)
                    if not info:
                        return
                    tree = info["tree"]
                    path = []
                    cur = node_id
                    while True:
                        node = tree.get_node(cur)
                        if node is None:
                            break
                        path.append(cur)
                        parent = tree.parent(cur)
                        if parent is None:
                            break
                        cur = parent.identifier
                    for nid in reversed(path):
                        iid = gui._multi_tree_iid(system_key, nid)
                        if not iid:
                            continue
                        try:
                            self.treeview.item(iid, open=True)
                        except Exception:
                            pass
                    target_iid = gui._multi_tree_iid(system_key, node_id)
                    if target_iid:
                        try:
                            self.treeview.selection_set(target_iid)
                            self.treeview.see(target_iid)
                        except Exception:
                            pass
                else:
                    tree = gui.tree_obj
                    if not tree:
                        return
                    path = []
                    cur = node_id
                    while True:
                        node = tree.get_node(cur)
                        if node is None:
                            break
                        path.append(cur)
                        parent = tree.parent(cur)
                        if parent is None:
                            break
                        cur = parent.identifier
                    for nid in reversed(path):
                        try:
                            self.treeview.item(nid, open=True)
                        except Exception:
                            pass
                    try:
                        self.treeview.selection_set(node_id)
                        self.treeview.see(node_id)
                    except Exception:
                        pass

            def recursive_close(self, iid):
                self.treeview.item(iid, open=False)
                for child in self.treeview.get_children(iid):
                    self.recursive_close(child)

        if self.display_mode == "multi":
            if not self.multi_trees:
                messagebox.showwarning("No Tree", "No tree loaded.")
                return
        else:
            if not self.tree_obj:
                messagebox.showwarning("No Tree", "No tree loaded.")
                return
        SearchWindow(self.root, self.treeview)

# ---------- Main Launch ----------
def launch_gui():
    root = tk.Tk()
    root.geometry("1280x820")
    # action = show_discover_properties_window(root)
    # if action == "discover":
    #     discover_required_properties()
    _app = SampleTreeGUI(root)
    root.mainloop()


# if __name__ == "__main__":
#     launch_gui()