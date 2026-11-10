"""Application bootstrap: connect to KiCad over IPC and launch the main window."""
import sys
import os
import logging
import tempfile
import kipy
import kipy.board

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from kipida.ui.main_window import MainWindow
from kipida.version import get_full_version

def main():
    # Setup logging to file for debugging
    log_file = os.path.join(tempfile.gettempdir(), 'kipida_entry.log')
    logging.basicConfig(filename=log_file, level=logging.DEBUG, filemode='w')
    logger = logging.getLogger("KiPIDA")
    logger.info(f"Ki-PIDA {get_full_version()} Entry Point Started")
    
    # 1. Connect to KiCad
    # Use our robust connection logic (same as in extractor.py)
    socket_path = os.environ.get("KICAD_API_SOCKET")
    if not socket_path:
        temp_dir = tempfile.gettempdir()
        potential_path = os.path.join(temp_dir, 'kicad', 'api.sock')
        
        # On Windows, skip existence check for named pipes
        if sys.platform == 'win32':
             socket_path = potential_path
        elif os.path.exists(potential_path):
             socket_path = potential_path
             
    # Ensure protocol prefix
    if socket_path and not socket_path.startswith('ipc://') and not socket_path.startswith('\\\\.\\pipe\\'):
         socket_path = f"ipc://{socket_path}"

    initial_logs = []
    def early_log(msg):
        logger.info(msg)
        initial_logs.append(msg)

    # early_log(f"Connecting to KiCad at {socket_path}")
    
    client = None
    board = None
    project = None
    
    try:
        # early_log("Initializing kipy client...")
        client = kipy.KiCad(socket_path=socket_path, timeout_ms=3000)
        # early_log("Requesting board object...")
        board = client.get_board()
        
        # Try to get project info from board.document without calling get_project
        # to avoid breaking board API access
        project = None
        if board and hasattr(board, 'document'):
            try:
                doc = board.document
                # Document is a protobuf DocumentSpecifier with a nested 'project' field
                
                # Create a simple object with path and name attributes
                class ProjectInfo:
                    def __init__(self, doc):
                        self.path = None
                        self.name = None
                        
                        # Extract the 'project' field from DocumentSpecifier
                        if hasattr(doc, 'ListFields'):
                            fields = doc.ListFields()
                            
                            for field_desc, value in fields:
                                field_name = field_desc.name
                                
                                # The 'project' field contains the project info
                                if field_name == 'project':
                                    # value is a protobuf with 'name' and 'path' attributes
                                    if hasattr(value, 'path'):
                                        self.path = value.path
                                    if hasattr(value, 'name'):
                                        self.name = value.name
                                    break
                        
                        # If we have path but no name, extract from path
                        if self.path and not self.name:
                            import os
                            self.name = os.path.basename(self.path)
                
                project = ProjectInfo(doc)
                if project.path:
                    early_log(f"Project: {project.name} at {project.path}")
                else:
                    early_log("Could not find project path in document")
                    project = None
            except Exception as e:
                early_log(f"Failed to get project info from board.document: {e}")
                import traceback
                early_log(traceback.format_exc())
                project = None
        else:
            early_log("Board has no document attribute")
        
        if board:
            early_log("Ki-PIDA connected successfully.")
        else:
            early_log("ERROR: client.get_board() returned None.")
    except Exception as e:
        early_log(f"Connection failed with error: {e}")
        import traceback
        early_log(traceback.format_exc())
        
    # 2. Initialize the Qt application
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Ki-PIDA")
    app.setApplicationVersion(get_full_version())
    resources = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")
    icon = QIcon()
    for size in (24, 48):  # Qt picks the best size for title bar / taskbar
        path = os.path.join(resources, f"kipida-{size}.png")
        if os.path.exists(path):
            icon.addFile(path)
    if not icon.isNull():
        app.setWindowIcon(icon)

    # 3. Create and show the main window
    try:
        window = MainWindow(board_adapter=board, project=project)
    except Exception as e:
        early_log(f"CRITICAL: Failed to create MainWindow: {e}")
        import traceback
        early_log(traceback.format_exc())
        QMessageBox.critical(None, "Startup Error", f"Failed to start Ki-PIDA: {e}\n\nCheck logs for details.")
        return

    # Pass initial logs to UI
    for msg in initial_logs:
        window.log(f"[INIT] {msg}")

    window.show()
    try:
        app.exec()
    except Exception as e:
        logger.error(f"Runtime error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
