# Start here: install and run OpenBedside

About 15 minutes the first time. You type the commands in **Terminal**
(Applications > Utilities > Terminal). Copy each line exactly. Linux is the same;
Windows notes are in [docs/USER-GUIDE.md](docs/USER-GUIDE.md).

## Step 0. Get OpenBedside

Either way ends with a folder called **OpenBedside** in your **Documents** folder.

- **Download:** go to **github.com/danielpettus/openbedside/releases/latest**, download
  `OpenBedside-<version>-kit.zip`, and double-click it. Rename the folder it makes to
  `OpenBedside` and move it into Documents.
- **Or with git:**

```
git clone https://github.com/danielpettus/openbedside.git ~/Documents/OpenBedside
```

## Step 1. Do you already have a new enough Python?

```
python3 --version
```

- **Python 3.11** or higher (3.12, 3.13, 3.14): skip to Step 3.
- 3.9 or 3.10, "command not found", or a box offering to install developer tools:
  do Step 2. (Cancel the pop-up box if you see one.) Most Macs ship with 3.9, which
  is too old.

## Step 2. Install Python from python.org

1. Go to **python.org/downloads/macos** in your browser.
2. Under the latest Python 3 release (3.14.7 as of August 2026), download the
   **macOS 64-bit universal2 installer**. It runs on Intel and Apple Silicon Macs.
3. Open the downloaded `.pkg` and click through the installer.
4. When it finishes, a Finder window opens. Double-click **Install Certificates.command**.
5. **Quit Terminal completely (Cmd-Q) and open it again**, then check:

```
python3 --version
```

## Step 3. Set up OpenBedside (once)

The Python environment lives in your home folder, not inside the OpenBedside folder.
If your Documents folder syncs through iCloud, this keeps thousands of small files
and a live database out of the sync.

```
python3 -m venv ~/.venvs/openbedside
source ~/.venvs/openbedside/bin/activate
pip install -e "$HOME/Documents/OpenBedside[dev]"
cd ~/Documents/OpenBedside && pytest && cd ~
```

- After `source`, your prompt starts with `(openbedside)`. That means you are in it.
- `pytest` should end with **36 passed**.
- If you put the OpenBedside folder somewhere other than Documents, change the path
  in the last two lines.

## Step 4. See it run: one window

```
openbedside demo
```

Open **http://127.0.0.1:8080** in your browser. You will see a simulated two-module
pump sending to a stand-in hospital system, and the window prints one line for every
message the hospital accepts. After three seconds the demo sends a made-up ADT admit
of patient TEST0001 into the simulator's bed, ICU 101 A, and the pump picks up its
patient. Look at the **Patients** page. Within a couple of minutes: a rate change on
module A, and the piggyback on module B finishing with the primary taking over by
itself.

**Ctrl-C** stops it.

## Step 5. The same, in separate tabs (to see every HL7 message)

Open tabs with **Cmd-T**. In **each** tab, first:

```
cd ~
source ~/.venvs/openbedside/bin/activate
```

| Tab | Command | What it is |
|---|---|---|
| 1 | `openbedside ehr-sim` | the stand-in hospital; prints every message in full |
| 2 | `openbedside run` | the gateway and pump simulator; page on :8080 |
| 3 | `openbedside send-order --module SIM-0001-A --drug heparin --rate 10 --vtbi 100` | a test order; answers "AE module is infusing", which is correct |

## Step 6. See a device integration: the fictional ACME pump

| Tab | Command |
|---|---|
| 1 | `openbedside fake-pump` |
| 2 | `openbedside ehr-sim` |
| 3 | `openbedside run -c ~/Documents/OpenBedside/config/acme-example.toml` |

The page shows the **ACME** adapter connected, but nothing is sent: the pump is not
registered. Go to **Devices**, click **Register** next to ACME-7731, fill in a unit,
room and bed if you like, and **Save**. Messages start flowing. Stop the fake pump
with Ctrl-C and watch the connection drop and, after 30 seconds, a
*communication-lost* event. Start it again and the adapter reconnects by itself.

To check an adapter without any hospital system:

```
openbedside check -c ~/Documents/OpenBedside/config/acme-example.toml --seconds 20 --show-hl7
```

To give the ACME pump a patient by ADT, tick "ADT: the patient in this device's bed"
when you register it, then in a spare tab:

```
openbedside send-adt --event A01 --mrn TEST0042 --unit ICU --room 204 --bed 1
```

using the unit, room and bed you registered.

## Step 7. Connect your own device

Read [docs/VENDOR-INTEGRATION-GUIDE.md](docs/VENDOR-INTEGRATION-GUIDE.md). It starts
with `openbedside new-adapter yourname`, which writes a starter folder for you.

## Every time after today

```
cd ~
source ~/.venvs/openbedside/bin/activate
openbedside demo
```

If macOS asks whether Python may accept incoming network connections, **Deny** is
fine. Everything runs on your own computer.
