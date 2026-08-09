import os
import time
import threading
import sqlite3
import pandas
import numpy as np
import vs_utilities as vsu
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

DB_NAME = "battery_telemetry.db"

INSTRUMENT_MAX_CURRENT = 1
INSTRUMENT_MIN_CURRENT = -1
INSTRUMENT_CHANNELS = ("Channel 1", "Channel 2")

CV_CURRENT_LIMIT = 0.5 # Determines the minimum current limit for the CV step during charge actions

class ExperimentDataFileHandler(FileSystemEventHandler):
    """
    Watches the experiment directory for the csv file on completion of the experiment.
    When the file is generated it calculates the new SOC and SOH of the battery and updates the log entries in the database.
    """
    def __init__(self, file_name, stop_event: threading.Event, log_id, battery_id):
        self.stop_event = stop_event
        self.file_name = file_name
        self.log_id = log_id
        self.battery_id = battery_id

    def on_created(self, event) -> None: # Handles when VersaStudio outputs the csv data when the experiment is complete
        print(f"File created: {event.src_path}")
        self.handle_file(event)        
    
    def on_modified(self, event) -> None: # For testing purposes when manually saving the file to the directory
        print(f"File modified: {event.src_path}")
        self.handle_file(event)

    def handle_file(self, event) -> None:
        # Proceed if this is the correct file
        if str(event.src_path).endswith(self.file_name + ".csv"):
            print(f"Experiment \"{self.file_name}\" (log: {self.log_id}) completed!")

            data = pandas.read_csv(event.src_path) # Open the csv file.
            # Get the current at every time interval
            current = data['Current (A)']
            elapsed_time = data['Elapsed Time (s)']
            voltage = data['Voltage (V)']
            delta_capacity = np.trapezoid(current, elapsed_time) # Integrate the current over time to get the change in capacity
            delta_capacity_mAh = delta_capacity * 1000 / 3600 # Convert from A*s to mAh
            print(f"The change in capacity was {delta_capacity_mAh} mAh.")

            # Update the database
            conn = None
            try:
                conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
                cursor = conn.cursor() # Create a cursor object to execute queries

                # First read the existing battery data
                cursor.execute("SELECT * FROM battery_logs where battery_id = ?", (self.battery_id))
                battery_history = cursor.fetchall()

                battery_info = get_battery_info(self.battery_id)
                rated_capacity = battery_info["rated_capacity"]
                soc = battery_info["soc"]
                soh = battery_info["soh"]
                remaining_capacity = battery_info["remaining_capacity"]
                actual_total_capacity = battery_info["actual_total_capacity"]

                # ============ ECC Algorithm ============
                dod = battery_info["dod"] # Initialize DOD to the previous value
                ddod = abs(delta_capacity_mAh / rated_capacity) # Calculate the change in DOD

                if len(battery_history) == 1:
                    # This is a special case where the battery is being initialized for the first time
                    # check if the action was charge/discharge or cycle
                    if battery_history[0][3] == "cycle":
                        # this means the battery started at an indeterminate charge
                        pass # TODO implement cycle initilization
                    else:
                        # this means the battery started at either full charge or zero charge
                        # and was either fully charged or fully discharged
                        soh = abs(delta_capacity_mAh) / rated_capacity
                        soc = 0 if current[0] < 0 else soh
                        dod = soh if current[0] < 0 else 0
                        actual_total_capacity = soh * rated_capacity

                else:
                    # Proceed with normal ECC logic for battery with accurate history
                    # Discharging Mode (I < 0):
                    if current[0] < 0:
                        dod += ddod # DOD increases during discharge
                        soc = soh - dod
                        # Was battery fully discharged?
                        if voltage[-1] <= battery_info["min_voltage"]:
                            soh = dod
                            actual_total_capacity = soh * rated_capacity # Calculate the new capacity
                            soc = 0 # Reset SOC at full discharge                        
                    
                    # Charging Mode (I > 0):
                    elif current[0] > 0:
                        dod -= ddod # DOD decreases during charge
                        dod = max(0.0, dod) # Prevent DOD from becoming negative due to drift
                        soc = soh - dod
                        # Was battery fully charged?
                        if voltage[-1] >= battery_info["max_voltage"]:
                            soh = soc
                            actual_total_capacity = soh * rated_capacity # Calculate the new capacity
                            dod = 0 # Reset DOD at full charge
                            
                remaining_capacity = soc * rated_capacity

                cursor.execute("""
                    UPDATE batteries
                    SET state_of_charge = ?,
                    remaining_capacity = ?,
                    state_of_health = ?,
                    actual_total_capacity = ?,
                    depth_of_discharge = ?
                    WHERE battery_id = ?
                """, (soc, remaining_capacity, soh, actual_total_capacity, dod, self.battery_id))
                conn.commit()

                cursor.execute("""
                    UPDATE battery_logs
                    SET duration = ?, soc = ?, soh = ?
                    WHERE log_id = ?
                """, (elapsed_time[-1], soc, soh, self.log_id))

            except sqlite3.Error as e:
                print(f"A sqlite error occurred while processing experiment log id {self.log_id} on battery {self.battery_id}: {e}")

            finally:
                if conn is not None:
                    conn.close()
            self.stop_event.set() # Stop the file watchdog

class EnhancedCoulombCounting():
    def __init__(self):
        """
        Using sqlite to create two tables:
        One to hold data about each batery,
        and one to keep logs of charge/discharge
        actions and track SOC and SOH
        """
        conn = None
        try:
            conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
            cursor = conn.cursor() # Create a cursor object to execute queries
            # Create the tables if they don't already exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS batteries (
                    battery_id TEXT PRIMARY KEY,
                    rated_capacity REAL NOT NULL,
                    max_voltage REAL NOT NULL,
                    min_voltage REAL NOT NULL,
                    max_charge_current REAL NOT NULL,
                    max_discharge_current REAL NOT NULL,
                    state_of_charge REAL NOT NULL,
                    remaining_capacity REAL NOT NULL,
                    state_of_health REAL NOT NULL,
                    actual_total_capacity REAL NOT NULL,
                    depth_of_discharge REAL NOT NULL
                )
            """)    # Rated capacity is determined by the manufacturer (it never changes)
                    # The remaining capacity is how much charge is left in the battery
                    # The actual_total_capacity is the maximum capacity of the battery after accounting for SOH degredation
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS battery_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    battery_id TEXT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    duration REAL,
                    soc REAL,
                    soh REAL,
                    FOREIGN KEY (battery_id) REFERENCES batteries (battery_id)
                )
            """)
            conn.commit()
            cursor.execute("""
                INSERT OR IGNORE INTO batteries (battery_id, rated_capacity, max_voltage, min_voltage, max_charge_current, max_discharge_current, state_of_charge, remaining_capacity, state_of_health, actual_total_capacity, depth_of_discharge)
                VALUES ('TEST', 1000, 4.2, 3.0, 2.0, 3.0, 100, 950, 99.98, 1000, 0)
            """)
            conn.commit()
            cursor.execute("""
                INSERT OR IGNORE INTO battery_logs (battery_id, timestamp, action, duration, soc, soh)
                VALUES ('TEST', '2023-06-15 10:00:00', 'Charge', 3600, 80.0, 95.0)
            """)
            conn.commit()

        except sqlite3.Error as e:
            print(f"An error occurred: {e}")

        finally:
            if conn is not None:
                conn.close()

        self.vsm = vsu.VersaStudioManager() # Create VersaStudio Manager Instance
        self.running_threads: list[threading.Thread] = []

    def get_battery_ids(self) -> list:
        """
        Returns a list of all battery IDs in the database
        """
        conn = None
        battery_ids = []
        try:
            conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
            cursor = conn.cursor() # Create a cursor object to execute queries
            # Fetch the battery IDs from the database
            cursor.execute("SELECT battery_id FROM batteries")
            entries = cursor.fetchall()
            # Return a list of only the IDs
            battery_ids = [entry[0] for entry in entries]

        except sqlite3.Error as e:
            print(f"An error occurred: {e}")

        finally:
            if conn is not None:
                conn.close()
        return battery_ids
        
    def add_battery(self, battery_id: str, rated_capacity: float, max_voltage: float, min_voltage: float, max_charge_current: float, max_discharge_current: float, soc: float = 100, remaining_capacity:float|None = None, soh: float = 100, actual_total_capacity:float|None = None) -> bool:
        """
        Add a battery to the database.
        Returns True if successful, False otherwise.
        """
        if remaining_capacity is None:
            remaining_capacity = rated_capacity * soc / 100
        if actual_total_capacity is None:
            actual_total_capacity = rated_capacity * soh / 100
        dod = 0 # Arbitrary for preinitialized battery

        conn = None
        try:
            conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
            cursor = conn.cursor() # Create a cursor object to execute queries
            cursor.execute("""
                INSERT INTO batteries (battery_id, rated_capacity, max_voltage, min_voltage, max_charge_current, max_discharge_current, state_of_charge, remaining_capacity, state_of_health, actual_total_capacity, depth_of_discharge)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (battery_id, rated_capacity, max_voltage, min_voltage, max_charge_current, max_discharge_current, soc, remaining_capacity, soh, actual_total_capacity, dod))
            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"An error occurred: {e}")
            return False

        finally:
            if conn is not None:
                conn.close()

    def write_log(self, battery_id, action, duration="NULL", soc="NULL", soh="NULL") -> int|None:
        """
        Writes a battery log entry to the database.
        Returns the log id of the new entry or None if it fails.
        """
        conn = None
        try:
            conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
            cursor = conn.cursor() # Create a cursor object to execute queries
            cursor.execute("""
                        INSERT INTO battery_logs (battery_id, timestamp, action, duration, soc, soh)
                        VALUES (?, ?, ?, ?, ?, ?)
            """, (battery_id, datetime.now().isoformat(), action, duration, soc, soh))
            conn.commit()
            return cursor.lastrowid

        except sqlite3.Error as e:
            print(f"An error occurred: {e}")
            return None

        finally:
            if conn is not None:
                conn.close()

    def run_action(self, battery_id: str, action: str, parameters: dict, instrument_index:int|None = None) -> bool:
        """
        Runs the specified action on the specified battery.
        Returns true if successful, and false otherwise.
        Possible actions: charge, discharge, cycle
        """
        # The UI allows input of high level parameters such as SOC, SOH, and cycles
        # but the required charge times and loop counts must be calculated based on
        # the battery's current charge.
        match action:
            case "charge":
                # For a charge action, the required parameters are:
                # Charge current (A) - This is already set in the UI
                # Voltage (V) - This will be the battery's charge cutoff voltage
                # CV Current (A) - This is the minimum current at which charging stops defined in 'CV_CURRENT_LIMIT'
                # Charge time (seconds) - Must be calculated based on the difference in SOC
                # -- If the desired charge is 100% then the time should be larger than the total charge time for 0->100%

                # First, verify the current is valid:
                if parameters["current"] < 0.0:
                    print(f"Failed to start charge! --Charge current ({parameters["current"]}A) must be greater than 0.")
                    return False

                # For parameters dependent on the battery's info, we need to get this data from the database
                battery_info = get_battery_info(battery_id)
                if battery_info == {}: # Ensure battery_info is populated
                    print(f"Failed to start charge! --Unable to get battery info for battery {battery_id}")
                    return False
                
                # Verify that the set current is within the battery's specs
                if parameters["current"] > battery_info["max_charge_current"]:
                    print(f"Failed to start charge! --Charge current ({parameters["current"]}A) exceeds this battery's maximum charge current ({battery_info["max_charge_current"]}A)")
                    return False

                # Set the voltage parameter to the battery's max voltage
                parameters["voltage"] = battery_info["max_voltage"]

                # Set the cv_current parameter to the value defined in 'CV_CURRENT_LIMIT'
                parameters["cv_current"] = CV_CURRENT_LIMIT

                # Calculate the time required to attain the specified SOC:
                target_soc = parameters["target_soc"]
                current_soc = battery_info["soc"]
                if target_soc == 100.0:
                    parameters["duration"] = 999999999 # If the target SOC is 100%, set the duration to a large number
                else:
                    if target_soc <= current_soc: # Ensure the target SOC is higher than the current value when charging
                        print(f"Failed to start charge! --The target SOC ({target_soc}%) must be greater than the current SOC ({current_soc}%)")
                        return False
                    delta_q = (target_soc - current_soc) * battery_info["rated_capacity"] # Calculate the difference in charge in mAh
                    charge_time = delta_q * (3600) / 1000 / parameters["current"] # Calculate the required charge time in seconds
                    parameters["duration"] = charge_time # Set the duration parameter

            case "discharge":
                # For a discharge action, the required parameters are:
                # Discharge current (A) - This is already set in the UI
                # Cutoff Voltage (V) - This is set in the battery info
                # Discharge time (seconds) - Must be calculated based on the difference in SOC
                # -- If the desired charge is 0% then the time should be larger than the total discharge time for 100->0%
                
                # First verify if the current is valid:
                if parameters["current"] > 0.0:
                    print(f"Failed to start discharge! --Discharge current must be less than zero not: {parameters["current"]}")
                    return False

                # For parameters dependent on the battery's info, we need to get this data from the database
                battery_info = get_battery_info(battery_id)
                if battery_info == {}: # Ensure battery_info is populated
                    print(f"Failed to start discharge! --Unable to get battery info for battery {battery_id}")
                    return False

                # Verify that the set current is within the battery's specs
                if -parameters["current"] > battery_info["max_discharge_current"]:
                    print(f"Failed to start discharge! --Discharge current ({parameters["current"]}A) exceeds this battery's maximum discharge current ({-battery_info["max_discharge_current"]}A)")
                    return False

                # Set the voltage parameter to the battery's minimum voltage
                parameters["voltage"] = battery_info["min_voltage"]

                # Calculate the time required to attain the specified SOC:
                target_soc = parameters["target_soc"]
                current_soc = battery_info["soc"]
                if target_soc == 0.0:
                    parameters["duration"] = 999999999 # If the target SOC is 0%, set the duration to a large number
                else:
                    if target_soc >= current_soc: # Ensure the target SOC is higher than the current value when charging
                        print(f"Failed to start disharge! --The target SOC ({target_soc}%) must be less than the current SOC ({current_soc}%)")
                        return False
                    delta_q = (current_soc - target_soc) * battery_info["rated_capacity"] # Calculate the difference in charge in mAh
                    discharge_time = delta_q * (3600) / 1000 / -parameters["current"] # Calculate the required charge time in seconds
                    parameters["duration"] = discharge_time # Set the duration parameter

            case "cycle":
                # TODO do dis sometime
                print(f"Nothing happened! --The cycle action hasn't been implemented yet.")
                pass
            case _:
                print(f"Failed to run action! --Invalid action: \"{action}\"")
                return False

        log_id = self.write_log(battery_id, action)
        if log_id is None:
            print(f"Failed to start {action}: Error writing log entry!")
            return False
        file_name = f"Battery_{battery_id}_{action}_{datetime.now().date()}_log_{log_id}"
        file_path = vsu.generate_experiment_file(action, file_name, parameters, f"Battery {battery_id}")
        # File path will be None if vsm failed to generate the experiment file
        if file_path is not None: # Ensure the file was generated successfully
            if self.vsm.open_experiment(file_path, instrument_index=instrument_index): # Ensure vsm is able to open the file
                if self.vsm.run_experiment(): # Ensure vsm runs the experiment successfully
                    experiment_directory = os.path.dirname(file_path)
                    
                    stop_event = threading.Event() # Event which tells the thread to exit forcefully
                    experiment_handler = ExperimentDataFileHandler(file_name, stop_event, log_id, battery_id) # Calculates battery parameters when the experiment is complete
                    observer = Observer()
                    observer.schedule(experiment_handler, experiment_directory, recursive=True)
                    observer.start()
                    print(f"Monitoring directory: {experiment_directory}")

                    # Use threading to run the watchdog loop in the background
                    def watchdog_loop(): # Loop monitors the directory for the experiment CSV file
                        try:
                            while not stop_event.is_set():
                                time.sleep(1)
                        finally:
                            observer.stop() # Stop the file watchdog
                            observer.join()
                            
                    thread = threading.Thread(target=watchdog_loop)
                    thread.start() # Start the watchdog loop in a separate thread

                    self.running_threads.append(thread) # Keep track of the thread so it doesn't get garbage collected
                    return True
                
                return False # If vsm fails to run the experiment
            return False # If vsm fails to open the experiment file
        return False # If vsm fails to generate the experiment file

    # def start_charge(self, battery_id: str, parameters: dict) -> bool:
    #     log_id = self.write_log(battery_id, "Charge")
    #     if log_id is None:
    #         print("Failed to start charge: Error writing log entry!")
    #         return False
    #     file_name = f"Battery_{battery_id}_Charge_CC-CV_{datetime.now().date()}_log_{log_id}"
    #     file_path = vsu.generate_experiment_file("charge", file_name, parameters, f"Battery {battery_id}")
    #     if file_path is not None:
    #         if self.vsm.open_experiment(file_path):
    #             path = os.path.dirname(file_path)
    #             stop_event = threading.Event()
    #             event_handler = ExperimentDataFileHandler(file_name, stop_event)
    #             observer = Observer()
    #             observer.schedule(event_handler, path, recursive=True)
    #             observer.start()
    #             print(f"Monitoring directory: {path}")
                
    #             # Use threading to run the watchdog loop in the background
    #             def watchdog_loop(): # Loop monitors the directory for the experiment CSV file
    #                 try:
    #                     while not stop_event.is_set():
    #                         time.sleep(1)
    #                 finally:
    #                     observer.stop() # Stop the file watchdog
    #                     observer.join()
                        
    #             thread = threading.Thread(target=watchdog_loop)
    #             thread.start() # Start the main loop in a separate thread
    #             self.running_threads.append(thread) # Keep track of the thread so it doesn't get garbage collected
    #             return True
    #         return False
    #     return False
    
    def close(self) -> None:
        self.vsm.close()

    def get_running(self) -> bool:
        """Returns True if any experiments are currently running and False otherwise."""
        self.running_threads = [thread for thread in self.running_threads if thread.is_alive()]
        if self.running_threads:
            return True
        else:
            return False

    def get_available_instruments(self) -> list:
        return self.vsm.get_available_instruments()

def get_battery_info(battery_id: str) -> dict:
    """
    Returns a dictionary containing information about the battery.
    - battery_id (str): The ID of the battery
    - rated_capacity (float): The rated capacity of the battery in mAh (never changes)
    - max_voltage (float): The maximum voltage of the battery in volts
    - min_voltage (float): The minimum voltage of the battery in volts
    - max_charge_current (float): The maximum charge current of the battery in amps
    - max_discharge_current (float): The maximum discharge current of the battery in amps
    - soc (float): The state of charge of the battery: the percent of remaining charge from its rated capacity
    - remaining_capacity (float): The remaining charge of the battery in mAh
    - soh (float): The state of health of the battery: the percent of actual total capacity from its rated capacity
    - actual_total_capacity (float): The actual total capacity of the battery which accounts for SOH degredation in mAh
    - dod (float): The depth of discharge of the battery in percent
    """
    conn = None
    try:
        conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
        cursor = conn.cursor() # Create a cursor object to execute queries
        cursor.execute("SELECT * FROM batteries where battery_id = ?", (battery_id,))
        battery_info_raw = cursor.fetchone()
        # Reference for raw battery info elements:
        # 0: battery_id TEXT PRIMARY KEY,
        # 1: rated_capacity_mah REAL NOT NULL,
        # 2: max_voltage REAL NOT NULL,
        # 3: min_voltage REAL NOT NULL,
        # 4: max_charge_current REAL NOT NULL,
        # 5: max_discharge_current REAL NOT NULL,
        # 6: state_of_charge REAL NOT NULL,
        # 7: remaining_capacity REAL NOT NULL,
        # 8: state_of_health REAL NOT NULL
        # 9: actual_total_capacity REAL NOT NULL,
        # 10: depth_of_discharge REAL NOT NULL,
        battery_info = {
            "id": battery_info_raw[0],
            "rated_capacity": battery_info_raw[1],
            "max_voltage": battery_info_raw[2],
            "min_voltage": battery_info_raw[3],
            "max_charge_current": battery_info_raw[4],
            "max_discharge_current": battery_info_raw[5],
            "soc": battery_info_raw[6],
            "remaining_capacity": battery_info_raw[7],
            "soh": battery_info_raw[8],
            "actual_total_capacity": battery_info_raw[9],
            "dod": battery_info_raw[10],
        }
        return battery_info

    except sqlite3.Error as e:
        print(f"A sqlite error occurred while getting info for battery {battery_id}: {e}")
        return {}

    finally:
        if conn is not None:
            conn.close()

# def get_battery_history(battery_id: str) -> list:
#     """
#     Returns a list of all the actions performed on the battery
#     """
#     conn = None
#     battery_data = []
#     try:
#         conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
#         cursor = conn.cursor() # Create a cursor object to execute queries
#         cursor.execute("SELECT * FROM battery_logs where battery_id = ?", (battery_id))
#         battery_data = cursor.fetchall()

#     except sqlite3.Error as e:
#         print(f"An error occurred: {e}")

#     finally:
#         if conn is not None:
#             conn.close()
    
"""
Boilerplate function def
    conn = None
    try:
        conn = sqlite3.connect("battery_telemetry.db") # Create a connection to the database
        cursor = conn.cursor() # Create a cursor object to execute queries

    except sqlite3.Error as e:
        print(f"An error occurred: {e}")

    finally:
        if conn is not None:
            conn.close()
"""

"""
Some Notes:

========== Abreviations ===========
SOC: (Absolute) State of Charge 
SOH: State of Health
DOD: Depth of Discharge
DDOD: Delta Depth of Discharge
CC-CV: Constant Current-Constant Voltage
ECC: Enhanced Coulomb Counting

========== ECC Algorithm ==========
1. Start by pulling battery data from the previous action
- If there is no previous action, assume SOH = 100 and run a charge & discharge then set SOH = DOD
2. Based on action:
    If charging to intermediate SOC:
    - Based on current SOC calculate time required to achive required DOD
    If charging till full use CC-CV:
    - Update SOH to match SOC
    If discharging to intermediate SOC:
    - Based on current SOC calculate time required to achive required DOD
    If discharging till empty use CC:
    - Update SOH to match DOD
3. After action is complete:
- DDOD = integral of current-time
- DOD = previous DOD - DDOD
- Record the estimates of each value in the actions log as well as the up-to-date info in the battery db
"""