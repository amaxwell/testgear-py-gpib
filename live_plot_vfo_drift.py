#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14

import datetime as dt
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import pyvisa
import sys, math

# Create figure for plotting
fig = plt.figure()
ax = fig.add_subplot(1, 1, 1)
xs = []
ys = []
vfo= []

rm = pyvisa.ResourceManager()

DATA_INTERVAL=30 # seconds
DATA_INTERVAL=1

# Input 2, 50 ohm (BNC)
INPUT_MODE="LOWZ"

# Input 1, Auto mode (N connector)
INPUT_MODE="AUTO"

# Input 2 1M (BNC)
INPUT_MODE="HIGHZ"

OUT_OF_RANGE="1E+38"

hp5350b = rm.open_resource("TCPIP::192.168.2.199::gpib0,14::INSTR")
hp5350b.set_visa_attribute(pyvisa.constants.VI_ATTR_TERMCHAR_EN, True)
hp5350b.set_visa_attribute(pyvisa.constants.VI_ATTR_TERMCHAR, 10)
hp5350b.read_termination = "\n"
hp5350b.write(INPUT_MODE)

start_datetime = dt.datetime.now()

SAMPLE_INTERVAL=500    # milliseconds
ROLLING_SAMPLE_TIME=10  # minutes

# cat /tmp/live_plot_vfo_drift.csv |pbcopy
output_file = open("/tmp/live_plot_vfo_drift.csv", "w")
output_file.write("date,VFO (MHz),drift (kHz)\n")

# This function is called periodically from FuncAnimation
def animate(i, xs, ys):

    # Add x and y to lists
    now = dt.datetime.now()
    elapsed_td = now - start_datetime
    
    #xs.append(dt.datetime.now().strftime('%H:%M:%S.%f'))
    xs.append(elapsed_td.total_seconds() / 60)

    hp5350b.assert_trigger()
    freq_s = hp5350b.read().strip()
    if freq_s == OUT_OF_RANGE:
        freq_s = "0"
    f = float(freq_s)
    
    if f > 10e6: # 40, 80
        f_vfo = (f - 5.5e6)/1e6
    else:
        f_vfo = (f + 5.5e6)/1e6
        
    vfo.append(f_vfo)
    ys.append((f_vfo - vfo[0])*1e3)
    
    #sys.stdout.write("%s,%f,%.3f\n" % (now, f_vfo, (f_vfo - vfo[0])*1e3))
    output_file.write("%s,%f,%.3f\n" % (now, f_vfo, (f_vfo - vfo[0])*1e3))
    output_file.flush()

    # Limit x and y lists to ROLLING_SAMPLE_TIME minutes
    n_samples_to_display = math.floor(ROLLING_SAMPLE_TIME*60*1000/SAMPLE_INTERVAL)
    
    xs = xs[-n_samples_to_display:]
    ys = ys[-n_samples_to_display:]

    # Draw x and y lists
    ax.clear()
    ax.plot(xs, ys)

    # Format plot
    #plt.xticks(rotation=45, ha='right')
    #plt.subplots_adjust(bottom=0.30)
    plt.title('Swan 700CX VFO drift over time')
    plt.ylabel('drift (kHz)')
    plt.xlabel("elapsed time (min)")
    plt.ylim(-2, 2)

if __name__ == '__main__':

    # Set up plot to call animate() function periodically
    ani = animation.FuncAnimation(fig, animate, fargs=(xs, ys), interval=SAMPLE_INTERVAL)
    plt.show()
