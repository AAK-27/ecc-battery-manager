import ctypes
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import sqlite3
from datetime import datetime
import vs_utilities as vsu
from ecc import *

# Flags to keep the system from sleeping while the app runs
ES_CONTINUOUS =       0x80000000
ES_SYSTEM_REQUIRED =  0x00000001

def prevent_sleep() -> None:
    """Prevents the system from sleeping."""
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

def allow_sleep() -> None:
    """Restores normal sleep behavior."""
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

def clear_frame(target_frame):
    """Clears the given frame of all widgets."""
    for widget in target_frame.winfo_children():
        widget.destroy()

def show_battery_details(main_content_frame, battery_id):
    """
    Displays the details of the current battery.
    If the battery is new it prompts the user to initialize it.
    """
    clear_frame(main_content_frame)

    # Load battery history from database
    battery_data = []
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM battery_logs where battery_id = ?", (battery_id,))
        battery_data = cursor.fetchall()
    # Don't catch the error because the following code won't execute without data anyways
    finally: # Close the database connection
        if conn is not None:
            conn.close()

    battery_info = get_battery_info(battery_id)
    if battery_info == {}: # Ensure battery_info isn't empty
        print(f"Failed to load battery ({battery_id}) info!")
        raise

    # ===== Initialization Screen =====
    if not battery_data:
        # If the battery has no history it needs to be initialized
        show_initialization_screen(main_content_frame, battery_id)
        return

    # ===== Main Battery View =====
    # Battery History
    history = tk.Frame(main_content_frame, width=300, bg="lightgrey")
    history.pack(side="right", fill="y", expand=False)
    history.pack_propagate(False)

    tk.Label(history, text="Battery History", font=("Arial", 16, "bold"), background="lightgrey").pack(pady=10)

    # Load battery logs
    for entry in battery_data:
        # Format the timestamp
        timestamp = datetime.fromisoformat(entry[2]).strftime("%Y-%m-%d %H:%M:%S")
        tk.Label(history, text=f"Time: {timestamp}", font=("Arial", 12), background="lightgrey").pack()
        tk.Label(history, text=f"Action: {entry[3]}", font=("Arial", 12), background="lightgrey").pack()
        tk.Label(history, text=f"Duration: {entry[4]}s", font=("Arial", 12), background="lightgrey").pack()
        tk.Label(history, text=f"State of Charge: {entry[5]}%", font=("Arial", 12), background="lightgrey").pack()
        tk.Label(history, text=f"State of Health: {entry[6]}%", font=("Arial", 12), background="lightgrey").pack()
        tk.Label(history, text="------------------------", font=("Arial", 12), background="lightgrey").pack()

    def update_action_parameters(action, parameters, parameters_frame):
        # Update the action parameter inputs based on the selected action

        clear_frame(parameters_frame) # clear the existing parameters

        match action:
            case "charge":
                tk.Label(parameters_frame, text="Target SOC:", font=("Arial", 12)).grid(row=0, column=0)
                target_soc_entry = tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["target_soc"])
                target_soc_entry.grid(row=0, column=1)
                target_soc_entry.focus_set()
                target_soc_entry.select_range(0, tk.END)
                tk.Label(parameters_frame, text="Current (A):", font=("Arial", 12)).grid(row=1, column=0)
                parameters["current"].set(min(battery_info["max_charge_current"], INSTRUMENT_MAX_CURRENT)) # Enter a default value for current and limit it to the instrument's max
                tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["current"], ).grid(row=1, column=1)
                # tk.Label(parameters_frame, text="CV Voltage (V):", font=("Arial", 12)).grid(row=2, column=0)
                # tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["charge_voltage"]).grid(row=2, column=1)
            case "discharge":
                tk.Label(parameters_frame, text="Target SOC:", font=("Arial", 12)).grid(row=0, column=0)
                target_soc_entry = tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["target_soc"])
                target_soc_entry.grid(row=0, column=1)
                target_soc_entry.focus_set()
                target_soc_entry.select_range(0, tk.END)
                tk.Label(parameters_frame, text="Current (A):", font=("Arial", 12)).grid(row=1, column=0)
                parameters["current"].set(min(battery_info["max_discharge_current"], INSTRUMENT_MAX_CURRENT)) # Enter a default value for current and limit it to the instrument's min
                tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["current"]).grid(row=1, column=1)
            case "cycle":
                tk.Label(parameters_frame, text="Current (A):", font=("Arial", 12)).grid(row=0, column=0)
                current_entry = tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["current"])
                current_entry.grid(row=0, column=1)
                current_entry.focus_set()
                current_entry.select_range(0, tk.END)
                # tk.Label(parameters_frame, text="CV Voltage (V)", font=("Arial", 12)).grid(row=1, column=0)
                # tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["charge_voltage"]).grid(row=1, column=1)
                # tk.Label(parameters_frame, text="Discharge Rate (A):", font=("Arial", 12)).grid(row=2, column=0)
                # tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["discharge_current"]).grid(row=2, column=1)
                tk.Label(parameters_frame, text="Cycle Count:", font=("Arial", 12)).grid(row=1, column=0)
                tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["cycle_count"]).grid(row=1, column=1)
                tk.Label(parameters_frame, text="Target SOH (optional):", font=("Arial", 12)).grid(row=2, column=0)
                tk.Entry(parameters_frame, font=("Arial", 12), textvariable=parameters["target_soh"]).grid(row=2, column=1)
            case _:
                pass

    # Actions Frame
    battery_frame = tk.Frame(main_content_frame, height=600)
    battery_frame.pack(side="top", fill="x", expand=False)
    battery_frame.pack_propagate(False)
    tk.Label(battery_frame, text=f"Battery {battery_id} Details", font=("Arial", 24)).pack(side="top", pady=(40, 30), expand=False)
    details_frame = tk.Frame(battery_frame, width=500, background="lightblue")
    details_frame.pack(side="left", fill="y", expand=False)
    details_frame.pack_propagate(False)
    actions_frame = tk.Frame(battery_frame)
    actions_frame.pack(side="bottom", fill="both", expand=True)

    soc = battery_info["soc"]
    soh = battery_info["soh"]
    atc = battery_info["actual_total_capacity"]
    
    tk.Label(details_frame, text=f"Current SOC: {soc}%", font=("Arial", 12)).pack(pady=5)
    tk.Label(details_frame, text=f"Current SOH: {soh}%", font=("Arial", 12)).pack(pady=5)
    tk.Label(details_frame, text=f"Actual Total Capacity: {atc}", font=("Arial", 12)).pack(pady=5)
    tk.Label(details_frame, text=f"Charge Cycles: {"--"}", font=("Arial", 12)).pack(pady=5)

    tk.Label(battery_frame, text="Run Actions", font=("Arial", 18)).pack(side="top", pady=(40, 30))
    # Action Selection
    tk.Label(actions_frame, text="Select Action:", font=("Arial", 12)).grid(row=0, column=0)
    
    parameters_frame = tk.Frame(actions_frame, highlightbackground="black", highlightthickness=1)
    parameters_frame.grid(row=1, column=0, columnspan=2, pady=10)
    parameters = {
        "current": tk.DoubleVar(),
        "cycle_count": tk.IntVar(),
        "target_soc": tk.DoubleVar(),
        "target_soh": tk.DoubleVar()
    }
    action_options = ["charge", "discharge", "cycle"]
    action_entry = ttk.Combobox(actions_frame, values=action_options, state="readonly", font=("Arial", 12))
    action_entry.grid(row=0, column=1)
    # Set the defualt value for the action
    if (battery_info["soc"] < 80.0):
        action_entry.set("charge") # Charge for batteries with low SOC
    else:
        action_entry.set("discharge") # Discharge for batteries with "full" SOC
    update_action_parameters(action_entry.get(), parameters, parameters_frame)
    action_entry.bind("<<ComboboxSelected>>", lambda event: update_action_parameters(action_entry.get(), parameters, parameters_frame))
    
    # Triggered when the run button is clicked
    def run_action(action: str, parameter_vars: dict):
        parameters = {}
        for key, value in parameter_vars.items():
            parameters[key] = value.get()

        if ecc.run_action(battery_id, action, parameters):
            print(f"Successfully started experiment ({action}) with Battery {battery_id} on instrument (?) with parameters:\n{parameters}")
        else:
            print(f"Failed to start experiment ({action}) with Battery {battery_id} on instrument (?) with parameters:\n{parameters}")

    tk.Button(actions_frame, text="Run", font=("Arial", 12), command=lambda: run_action(action_entry.get(), parameters)).grid(row=2, column=0, columnspan=2, pady=10)

    # Graph Frame
    graph_frame = tk.Frame(main_content_frame, highlightbackground="black", highlightthickness=1)
    graph_frame.pack(side="bottom", fill="both", expand=True)


def show_creation_form(main_content_frame, sidebar_frame, add_button):
    # Function to display the battery creation form
    
    clear_frame(main_content_frame)

    # Header
    tk.Label(main_content_frame, text="Register New Battery", font=("Arial", 24)).pack(pady=(40, 30))

    # Form Container Frame
    form_frame = tk.Frame(main_content_frame)
    form_frame.pack(pady=10)

    # Input fields
    # Battery ID
    tk.Label(form_frame, text="Battery ID:", font=("Arial", 12)).grid(row=0, column=0, sticky="w", pady=10, padx=10)
    id_var = tk.StringVar() # Create a string variable to hold the input value
    id_entry = tk.Entry(form_frame, textvariable=id_var, font=("Arial", 12))
    id_entry.grid(row=0, column=1, pady=10, padx=10)
    id_entry.focus_set() # Set focus to the entry field
    # Rated Capacity (mAh)
    tk.Label(form_frame, text="Rated Capacity (mAh):", font=("Arial", 12)).grid(row=1, column=0, sticky="w", pady=10, padx=10)
    rated_capacity_var = tk.IntVar() # Create an integer variable to hold the input value
    rated_capacity_entry = tk.Entry(form_frame, textvariable=rated_capacity_var, font=("Arial", 12))
    rated_capacity_entry.grid(row=1, column=1, pady=10, padx=10)
    # Max Voltage (V)
    tk.Label(form_frame, text="Max Voltage (V):", font=("Arial", 12)).grid(row=3, column=0, sticky="w", pady=10, padx=10)
    max_voltage_var = tk.DoubleVar() # Create a double variable to hold the input value
    max_voltage_entry = tk.Entry(form_frame, textvariable=max_voltage_var, font=("Arial", 12))
    max_voltage_entry.grid(row=3, column=1, pady=10, padx=10)
    # Min Voltage (V)
    tk.Label(form_frame, text="Min Voltage (V):", font=("Arial", 12)).grid(row=4, column=0, sticky="w", pady=10, padx=10)
    min_voltage_var = tk.DoubleVar() # Create a double variable to hold the input value
    min_voltage_entry = tk.Entry(form_frame, textvariable=min_voltage_var, font=("Arial", 12))
    min_voltage_entry.grid(row=4, column=1, pady=10, padx=10)
    # Max Charge Current (A)
    tk.Label(form_frame, text="Max Charge Current (A):", font=("Arial", 12)).grid(row=5, column=0, sticky="w", pady=10, padx=10)
    max_charge_current_var = tk.DoubleVar() # Create a double variable to hold the input value
    max_charge_current_entry = tk.Entry(form_frame, textvariable=max_charge_current_var, font=("Arial", 12))
    max_charge_current_entry.grid(row=5, column=1, pady=10, padx=10)
    # Max Discharge Current (A)
    tk.Label(form_frame, text="Max Discharge Current (A):", font=("Arial", 12)).grid(row=6, column=0, sticky="w", pady=10, padx=10)
    max_discharge_current_var = tk.DoubleVar() # Create a double variable to hold the input value
    max_discharge_current_entry = tk.Entry(form_frame, textvariable=max_discharge_current_var, font=("Arial", 12))
    max_discharge_current_entry.grid(row=6, column=1, pady=10, padx=10)
    
    def register_battery():
        # Get the values from the entry fields
        battery_id = id_var.get()
        rated_capacity = rated_capacity_var.get()
        max_voltage = max_voltage_var.get()
        min_voltage = min_voltage_var.get()
        max_charge_current = max_charge_current_var.get()
        max_discharge_current = max_discharge_current_var.get()
        # Add the battery to the database
        ecc.add_battery(battery_id, rated_capacity, max_voltage, min_voltage, max_charge_current, max_discharge_current)
                    
        # Clear the id entry field only so you can quickly register a bunch of the same battery
        id_var.set("")
        id_entry.focus_set() # Set focus back to the id entry field

        # Show a success message
        success_label = tk.Label(main_content_frame, text="Battery registered successfully!", fg="green", font=("Arial", 12))
        success_label.pack(pady=10)

        # Add the new battery to the sidebar
        new_btn = tk.Button(sidebar_frame, text=f"Battery {battery_id}", command=lambda id=battery_id: show_battery_details(main_content_frame, id))
        new_btn.pack(fill="x", padx=5, ipady=10, pady=5)
        # Destroy the "Add Battery" button and recreate it
        add_button.destroy()
        new_add_button = tk.Button(sidebar_frame, text="Add Battery", command=lambda: show_creation_form(main_content_frame, sidebar_frame, new_add_button))
        new_add_button.pack(fill="x", padx=5, ipady=10, pady=5)

        print("Successfully added battery!")

    # Submit Button
    tk.Button(form_frame, text="Register Battery", font=("Arial", 12), command=register_battery).grid(row=7, columnspan=2, pady=20)

def show_initialization_screen(main_content_frame, battery_id):
    init_frame = tk.Frame(main_content_frame)
    init_frame.pack(side="top", fill="both")
    # Prompt the user to complete initialization by entering SOH characterization parameters
    tk.Label(init_frame, text=f"Complete initialization on battery {battery_id}.", font=("Arial", 16)).grid(row=0, columnspan=2, sticky="w", pady=10)
    tk.Label(init_frame, text="Current Charge:", font=("Arial", 12)).grid(row=1, column=0, sticky="w", pady=10, padx=10)
    current_charge_entry = ttk.Combobox(init_frame, values=["full", "discharged", "indeterminate"], state="readonly", font=("Arial", 12))
    current_charge_entry.grid(row=1, column=1, pady=10, padx=10)
    current_charge_entry.set("indeterminate")
    tk.Label(init_frame, text="Instrument:", font=("Arial", 12)).grid(row=2, column=0, sticky="w", pady=10, padx=10)
    instruments = ecc.get_available_instruments() # Returns a list of instruments where each instrument is a dictionary with name, label, index, and available
    instrument_names = [instrument['name'] for instrument in instruments] # Get the names of each instrument so the user can select by name
    instrument_entry = ttk.Combobox(init_frame, font=("Arial", 12), values=instrument_names, state="readonly")
    instrument_entry.grid(row=2, column=1, pady=10, padx=10)

    def run_initialization():
        current_charge = current_charge_entry.get()
        action = "charge"
        parameters = {}
        match current_charge:
            case "full":
                action = "discharge"
                parameters["target_soc"] = 0
                parameters["current"] = -1
            case "discharged":
                action = "charge"
                parameters["target_soc"] =100
                parameters["current"] = 1
            case "indeterminate":
                print("Not yet implemented, just manually fully charge or discharge the battery before initializing!")
                # action = "cycle"
            case _:
                print("You must select the current charge!")
                return

        # Get the index of the chosen instrument to select it for the experiment
        instrument_name = instrument_entry.get()
        if not instrument_name: # Ensure the user selected an instrument
            print("You must select an instrument!")
            return
        instrument_index, = [instrument['index'] for instrument in instruments if instrument['name'] == instrument_name]

        print(f"Running {action} on battery {battery_id} with parameters {parameters} on instrument {instrument_index} for initialization.")
        ecc.run_action(battery_id, action, parameters, instrument_index)

    tk.Label(init_frame, text="Ensure battery is connected to instrument before running!", font=("Arial", 16), fg="red").grid(row=3, columnspan=2, pady=10)
    tk.Button(init_frame, text="Run Initialization", font=("Arial", 12), command=run_initialization).grid(row=4, columnspan=2, pady=20)


def main():
    global ecc
    # Create the main window
    root = tk.Tk()
    root.state('zoomed') # Maximize the window
    root.attributes('-topmost', True) # Force it to the front of the screen

    ecc = EnhancedCoulombCounting() # Initialize the battery database

    # Create a frame for the sidebar
    sidebar = tk.Frame(root, width=300, bg='lightgray')
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False) # Stop the sidebar from shrinking to fit its contents
    # Using place which allows percentages for sizing
    # sidebar.place(relwidth=0.15, relheight=1.0, relx=0.0, rely=0.0, anchor='nw')

    # Create a frame for the main content
    main_content = tk.Frame(root)
    main_content.pack(side="right", fill="both", expand=True)
    # Add defualt label to main area
    default_label = tk.Label(main_content, text="Select a battery to get started", font=("Arial", 24))
    default_label.pack(expand=True)

    # Fetch all the battery IDs from the database
    battery_ids = ecc.get_battery_ids()

    # Add a button for each battery to the sidebar
    for battery_id in battery_ids: # Add a button to the sidebar for each battery
        btn = tk.Button(sidebar, text=f"Battery {battery_id}", command=lambda id=battery_id: show_battery_details(main_content, id))
        btn.pack(fill="x", padx=5, ipady=10, pady=5)

    # Add a button to register new batteries
    addBatteryBtn = tk.Button(sidebar, text="Add Battery", command=lambda: show_creation_form(main_content, sidebar, addBatteryBtn))
    addBatteryBtn.pack(fill="x", padx=5, ipady=10, pady=5)

    def on_closing(): # Called when attempting to close tkinter window
        if ecc.get_running(): # Check if any experiments are in progress
            # Warn the user that an experiment is running
            messagebox.showwarning("Warning", "An experiement is in progress! Wait for it to finish before closing.")
        else:
            root.destroy() # Close the tkinter window
            ecc.close()
            allow_sleep() # Allow the computer to sleep

    root.protocol("WM_DELETE_WINDOW", on_closing) # Set the close button to call on_closing()

    try:
        prevent_sleep() # Prevent the computer from sleeping
        root.attributes('-topmost', False) # Allow other windows to open in front of the main window
        root.mainloop() # Tkinter mainloop blocks the main thread until the window is closed
    except KeyboardInterrupt:
        pass
    finally: # when the tkinter window is closed, the main thread proceeds
        ecc.close()
        allow_sleep()

if __name__ == "__main__":
    main()