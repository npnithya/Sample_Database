from database_GUI import launch_gui
launch_gui()

### to build as exe, run:
#   pyinstaller --onedir -w --icon=db.ico 'database_explorer.py'
# in this directory

# Features to add:
# - add a "delete" button to delete the current entry (make sure to ask for confirmation before deleting)
# / make a global searcher (and maybe a global tree?)
# / add check box to return partial matches, and add automatic searching of b-6 and e-c character swaps
# / add a "copy node" to copy the current node to another node, opening the node editor dialogue with the same paramaters already filled in
# / add "searching..." text when searching, and maybe a timeout warning if the search takes too long
# / add sorting options to the tree view (probably limited to sorting by name or date created)
# / maybe add a side panel to show the properties of the currently selected node, instead of the "View Properties"