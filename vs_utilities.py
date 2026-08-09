"""
Some functions to help interface with AMETEK VersaStudio
"""

import time
import os
from pywinauto import Application
from pywinauto import ElementAmbiguousError
from pywinauto.timings import TimeoutError

VERSASTUDIO_PATH = r"C:\Program Files\Princeton Applied Research\VersaStudio\VersaStudio.exe"
EXPERIMENT_TEMPLATE_DIR = r"C:\My Data\VersaStudio\Data\VSPlus\templates"
EXPERIMENT_DATA_DIR = r"C:\My Data\VersaStudio\Data\VSPlus\experiments"

CWD = os.getcwd()

DEFAULT_TIME_PER_POINT = 20 # The rate at which VersaStudio will record data in seconds
# --this default value will only be used if the tpp parameter is not defined in generate_experiment_file()

class VersaStudioManager():
    def __init__(self):
        # Start the VersaStudio application and get a refrence to the window
        try:
            self.app = Application(backend="win32").start(VERSASTUDIO_PATH)
            self.window = self.app.window(title_re=".*VersaStudio.*")
            # UIA Backend for access to run button
            self.app_uia = Application(backend="uia").connect(process=self.app.process)
            self.window_uia = self.app_uia.window(title_re=".*VersaStudio.*")
        except Exception as e:
            print(f"Error occured while starting VersaStudio: {e}\nEnsure the VERSASTUDIO_PATH is correct!")
            raise SystemExit(1)
        self.window.minimize() # Minimize it immediately so it doesn't interfere with active work

        self.instruments = {
            0: {
                'index': 0,
                'name': "No Instrument",
                'label': "No Instrument",
                'available': True,
                'physical': False
            },
            1: {
                'index': 1,
                'name': "Channel 1",
                'label': "23228002-CH1",
                'available': True,
                'physical': True
            },
            2: {
                'index': 2,
                'name': "Channel 2",
                'label': "23228002-CH2",
                'available': True,
                'physical': True
            }
        }

    def close(self):
        try:
            if not self.window.exists():
                return
        except ElementAmbiguousError:
            pass # This means it found more than one window so the app is still running
        self.window = self.app.top_window()
        try: # Try closing the main window
            self.window.close()
        except Exception as e:
            print(f"Error occured while closing VersaStudio: {e}")
            # If that fails just kill the process
            self.app.kill()
            print("Killed VersaStudio process.")

    def get_available_instruments(self) -> list:
        """
        Returns a list of available instruments. Other than 'No Instrument' which is always available.
        """
        return [instrument for instrument in self.instruments.values() if instrument['available'] and instrument['physical']]

    def open_experiment(self, file_path:str, keep_open:bool = False, instrument_index:int|None = None) -> bool:
        """
        Opens an experiment file in VersaStudio.
        Returns True if the file opened successfully and False otherwise. 
        """
        self.window.maximize()

        # If an instrument was specified check if it is actually available
        if instrument_index:
            if not self.instruments[instrument_index]['available']:
                print(f"WARNING! Instrument {instrument_index} is not available!\nFailed to open experiment.")
                return False
        else: # If an instrument wasn't specified try to use the next available instrument
            for key, value in self.instruments.items():
                if value['available']:
                    instrument_index = key
                    break
            else: # If no instruments are available then don't do anything
                print("WARNING! No instruments available!\nFailed to open experiment.")
                return False
    
        self.select_instrument(instrument_index, True) # Select the instrument to use for the experiment
        
        try:
            self.window.menu_select("Experiment->Open")
            open_dialog = self.app.window(title="Open Experiment")
            open_dialog.Edit0.set_text(file_path) # Enter the file path into the text box
            open_dialog.Open.wait("enabled", timeout=5) # Make sure the open button is enabled before trying to click
            open_dialog.Open.wait("ready", timeout=5)
            open_dialog.Open.click_input()

            time.sleep(1) # Wait a moment for the file to load

            try: # Check if there was an error with opening the file
                warning = self.app.window(title="Error in Open File")
                warning.wait("ready", timeout=3) # Will wait 3 seconds for the warning to appear. If no warning appears it throws a timeout error
                print("Error in opening file")
                warning.OK.click()
                return False
            except TimeoutError:
                pass # If no warning appears, continue

            return True # If no error occured the file loaded 

        except Exception as e:
            print(f"Error occured while opening experiment: {e}")
            return False

        finally: # When the file loads the window changes, so we need a new reference
            self.window = self.app.window(title_re=".*VersaStudio.*")
            self.window_uia = self.app_uia.window(title_re=".*VersaStudio.*")
            if not keep_open: self.window.minimize()

    def select_instrument(self, instrument: int, keep_open: bool = False) -> bool:
        """
        Selects the corresponding instrument in VersaStudio.
        0: No instrument
        1: Channel 1
        2: Channel 2
        """
        self.window.maximize()
        # Ensure the instrument number is valid
        if instrument not in self.instruments:
            print(f"WARNING! Invalid instrument number: {instrument}\nFailed to switch instrument.")
            if not keep_open: self.window.minimize()
            return False

        instrument_label = self.instruments[instrument]['label']
        
        try:
            # Navigate through the menu to open the instrument selection pane
            self.window.menu_select("Tools->Select Instrument")
            # Use the UIA backend to search for the instrument label
            instrument_ui_label = self.window_uia.child_window(title=instrument_label, auto_id="lbName")
            instrument_ui_label.double_click_input() # you have to double click the label to select it
            return True
        except Exception as e:
            print(f"Error selecting instrument: {e}")
            return False
        finally:
            if not keep_open: self.window.minimize()

    def run_experiment(self) -> bool:
        try:
            run_button = self.window_uia.child_window(title_re=".*Run.*", control_type="Button")
            run_button.click()
            return True
        except Exception as e:
            print(f"WARNING! An error occured in run_experiment: {e}\nFailed to run experiment.")
            return False

def generate_experiment_file(action_type: str, filename: str, parameters: dict, subdir: str = "") -> str|None:
    """
    Generate an experiment file with the given parameters.
    Returns the path to the generated file, or None if the file could not be generated.

    parameters:
    current
    voltage
    cv_current
    duration
    """
    template_file = None
    match action_type: # Get the templete file path for the chosen action 
        case "charge":
            template_file = os.path.join(CWD, r"Experiment Templates\CC-CV_VSPlus_template.par")
        case "discharge":
            template_file = os.path.join(CWD, r"Experiment Templates\CC_VSPlus_template.par")
        case "cycle":
            template_file = os.path.join(CWD, r"Experiment Templates\True_Charge_Cycle_VSPlus_template.par")
        case _: # If a valid action wasn't selected, abort the file creation
            print(f"WARNING! Invalid action type: {action_type}\nFailed to generate experiment file.")
            return None

    # Ensure the time per point parameter is set or else the proceeding code will raise an exception
    if "tpp" not in parameters:
        parameters["tpp"] = DEFAULT_TIME_PER_POINT
    
    try: # Open the template file in read mode
        with open(template_file, "r") as f:
            template = f.read()
            for key, value in parameters.items(): # Replace the placeholders with the given values
                placeholder = "{" + key + "}"
                template = template.replace(placeholder, str(value))

            parameter_placeholder_index = template.find("{") # Check if any placeholders are left in the file, and if so raise an error
            if parameter_placeholder_index != -1:
                missing_parameter = template[parameter_placeholder_index+1:template.find("}")]
                # We need to raise an error because attempting to load an invalid experiment file in VersaStudio will cause it to erase the file and freeze
                raise ValueError(f"Missing required {action_type} action parameter ({missing_parameter})")
            
            os.makedirs(os.path.join(EXPERIMENT_DATA_DIR, subdir), exist_ok=True) # Create the experiment data directory if it doesn't exist

            file_path = os.path.join(EXPERIMENT_DATA_DIR, subdir, filename + ".par")
            
            with open(file_path, "w") as f: # Write the modified template to a new file
                f.write(template)
                return file_path

    except FileNotFoundError:
        # Handle the case where the template file doesn't exist
        print(f"WARNING! Template file not found: {template_file}\nFailed to generate experiment file.")
        return None
    