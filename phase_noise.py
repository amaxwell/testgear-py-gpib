#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14

#
# This will be a moving target until I figure out what I want to do with it. Presently
# I'm plotting using DataGraph, since it's easy to make plots that look decent. The
# interface of setting variables in code is kind of lame, though, and I usually forget
# to update the note field.
#
# The logic of decades and the looping methodology is inspired by John Miles KE5FX
# PN.EXE, which was extremely helpful. http://www.ke5fx.com/gpib/pn.htm
#
# There are some differences, notably in recentering (suggested by HP's AN 270-2),
# and in pulling the cal constants from the analyzer. I also only support the
# Tek 2756P, because that's what I have, so no need to maintain compatibility with
# less capable instruments. I strongly suspect this would work as-is with the 494P
# series and any Tek275x analyzer.
#
# http://www.ke5fx.com/gpib/an270-2.pdf
# http://www.ke5fx.com/HP_PN_seminar.pdf
# 
# The impetus for this is 1) try and understand phase noise measurement by forcing
# myself to debug it and 2) avoid using Windows, since I have to run it under VMWare.
# PN.EXE also doesn't work great with AR488, since AR488 times out on long sweeps (and
# I suspect the Prologix would fail as well). For posterity (me), here is how I had to
# configure AR488 to work with PN.EXE, using TeraTerm. DO NOT USE GPIB CONFIGURATOR!
#
# ++addr 7
# ++auto 1
# ++eoi 1
# ++eos 2
# ++read_tmo_ms 32000
# ++id verstr AR488 GPIB-USB version 6.1 
# ++savecfg
# 
# 

from math import log10, pow
import sys
from datetime import datetime
import numpy as np
import pyvisa

from savitzky_golay import savitzky_golay
from tek2756 import Tektronix2756P        

# 
# Was playing with this as a way to process the overlap
# regions before smoothing, since some frequencies have
# two values.
#
def average_at_duplicate_frequencies(freq, mag):
    
    freq = np.array(freq)
    mag = np.array(mag)
    
    m = np.zeros_like(freq, dtype=bool)
    m[np.unique(freq, return_index=True)[1]] = True
    indexes = np.where(~m)[0]
    averages = (mag[indexes] + mag[indexes-1])/2
    
    mag[indexes] = averages
    mag[indexes-1] = averages
    
    m[np.unique(freq, return_index=True)[1]] = True
    
    return freq[m], mag[m]

#
# This is the workhorse function, but it's really just a wrapper around CURVE
# that handles the appropriate power and frequency scaling, after breaking the
# frequency range up into decades and adjusting span and RBW appropriately.
#
def scaled_phase_noise(sa, nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip=0, vbw="0"):
    
    pn_x = []
    pn_y = []
    
    tuned_carrier = nominal_carrier

    # center frequency; we start measuring at the offset, not at the peak,
    # so we tune to the right of the carrier
    tune_freq = tuned_carrier + min_offset
    
    # FIXME: use measured?
    sa.set_reflevel(carrier_level)
    sa.set_vbw(vbw)
    
    # initialize to nominal level in case retune_carrier is False
    measured_carrier_level = carrier_level
    
    if clip and carrier_level < 0:
        sa.set_reflevel(carrier_level + clip)
        print("Clipping signal with ref level:", carrier_level + clip)
    elif clip:
        assert carrier_level < 0, "refusing to clip carrier at level 0 dBm or greater" 

    min_decade = int(log10(min_offset) + 0.5)
    max_decade = int(log10(max_offset) + 0.5)
    #print("decades:", min_decade, max_decade)
   
    #print("min offset Hz", pow(10.0, min_decade) + 0.5)
    #print("max offset Hz", pow(10.0, max_decade + 1) + 0.5)
    
    pn_x = []
    pn_y = []
    
    # list of 2, 3, 4, 5, etc; not a starting frequency
    for current_decade in range(min_decade, max_decade):
        
        # separator for decades; can probably disable the log goo at this point   
        print("xxxxxxxxxxxxxxx BEGIN SWEEP: DECADE %d" % (current_decade))

        # 100-1,000, 1,000-10,000, 10,000-100,000
        # decade_start is our offset from the carrier, and it will be a decade wide
        decade_start = int(pow(10.0, current_decade) + 0.5)        
        total_span = decade_start * 10
    
        rbw = int(decade_start / 10)
        sa.set_rbw(rbw)
   
        print("decade %s Hz to %s Hz offset" % (decade_start, decade_start + total_span))
        print("RBW: %d Hz, Span/Div: %d Hz" % (rbw, total_span / 10))
    
        # set_span requires span/div
        sa.set_span(total_span / 10, units="HZ")
    
        # jog frequency by half the total span (starts at carrier + decade_start)
        # check carrier and retune each decade in case of slow drift
        if retune_carrier:
            tuned_carrier, measured_carrier_level = sa.carrier_near(tuned_carrier)
            sys.stderr.write("Found carrier %.2f dBm at %d Hz\n" % (measured_carrier_level, tuned_carrier))
        
            # hit this with the HP 8620C; drifted so far I couldn't find it
            assert abs(abs(carrier_level) - abs(measured_carrier_level)) < 10, "*** ERROR *** no carrier detected within 10 dB of nominal %d dBm near %d" % (carrier_level, tuned_carrier)
            
            # warn in case of drift; tried 10 Hz and hit that immediately with
            # an old Tek 067-0532-00 leveled sine generator
            if abs(tuned_carrier - nominal_carrier) > total_span / 4:
                sys.stderr.write("*** WARNING *** Carrier drifted more than %d Hz from nominal: %d\n" % (total_span/4, tuned_carrier))
        
        center_frequency = tuned_carrier + decade_start + total_span/2
        sa.set_center_frequency(center_frequency, units="HZ")
        print("center at %s for carrier at %s" % (center_frequency, tuned_carrier))
    
        scaled_x, scaled_y = sa.curve()
    
        # HP uses -10 * log10(1.2 * rbw) as starting estimate. KE5FX uses an additional
        # additive factor, but not the 1.2. This just confused me, so I pull the cal
        # constants for this machine and use them directly.

        # Need to subtract this value of noise bandwidth correction (in dB, per manual),
        # which matches the HP PN seminar notes. Had the sign wrong here initially and
        # got super confused because the values are so much larger than the ones in pn.cpp.
        f_corr = -1 * sa.filter(rbw).noise_bandwidth_F
        #f_corr = -10 * log10(1.2 * rbw)
        print("F corr dB:", f_corr)
    
        # FIXME check this; HP default value. Probably reasonable, given how close
        # the -10 * log10(1.2 * rbw) value is to our actual constants. KE5FX says
        # the Tek SA's track this internally, so it should be zero.
        Cn = 0
            
        # Extend the previous results; sort at the end because of overlap.
        # Compute frequency offset relative to most recently measured carrier
        # in case retune_carrier is true.
        for sx, sy in zip(scaled_x, scaled_y):
            pn_x.append(sx - tuned_carrier)
            pn_y.append(sy - measured_carrier_level + f_corr + Cn)
    
    # get rid of duplicate frequency in the offset regions
    # pn_x, pn_y = average_at_duplicate_frequencies(pn_x, pn_y)
    
    return pn_x, pn_y, tuned_carrier

# 
# Tried this before settling on Savitzky-Golay
# 
def box_smooth(x, y, box_length=101):
    
    box = np.ones(box_length) / box_length
    out_x = np.convolve(x, box, mode="valid")
    out_y = np.convolve(y, box, mode="valid")
    
    return out_x, out_y

def noise_floor():
    
    ip_address = "192.168.2.199"
    gpib_address = 7
    rm = pyvisa.ResourceManager()
    rsrc = rm.open_resource("TCPIP::%s::gpib0,%d::INSTR" % (ip_address, gpib_address))
    
    sa = Tektronix2756P(rsrc)
    sa.save_state()
    
    # TODO: check RFATT, figure out MINATT and MAXPWR, although
    # it's probably too late by the time the user has plugged
    # in the cables.
    
    # lazy way to make sure we have 10 dB/div, auto resbw, etc
    sa.reset()
    
    sa.set_time_auto()
    
    # this is a key setup step of PN.EXE; I thought reset would
    # set it, but apparently I had that backwards.
    sa.set_cursor_avg()
    
    note = "RF noise floor"
    nominal_carrier = 100e6 #100e6
    carrier_level = -80
    retune_carrier = False
    min_offset = 100
    max_offset = 1e6
    clip = 0
    vbw = "0"
    
    runs_to_average = 1
    list_of_runs = []
    
    # pn_x is same for all runs; stash the last one here if we're averaging
    pn_x = None
    pn_y = None
        
    try:
        for idx in range(runs_to_average):
            print("*** starting run %d of %d" % (idx + 1, runs_to_average))
            pn_x, pn_y, ignored = scaled_phase_noise(sa, nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip=clip, vbw=vbw)
            # since we lied about carrier_level for RF noise floor to raise the
            # trace up, push it back down here to save a postprocessing step
            pn_y = [y + carrier_level for y in pn_y]
            list_of_runs.append(pn_y)
            
        pn_y = np.average(list_of_runs, axis=0) if runs_to_average > 1 else list_of_runs[0]
        
        output_name = "phase_noise_py.%s.csv" % (datetime.now().strftime("%Y-%m-%d %H%M"))
        with open(output_name, "w") as outf:
            
            # log RFATT before restoring state, since it depends on clip level
            outf.write("# note: %s\n# runs averaged: %d\n# nominal_carrier: %f Hz\n# carrier_level: %d dBm\n# retune_carrier: %d\n# min_offset: %d Hz\n# max_offset: %d Hz\n# clip: %d\n# vbw: %s\n# rfatt: %s dB\n#\n" % (note, runs_to_average, nominal_carrier, 0, retune_carrier, min_offset, max_offset, clip, vbw, sa.rfatt()))
            
            outf.write("f (Hz),ℒ (dBc/Hz),f_smooth (Hz),ℒ_smooth (dBc/Hz)\n") 

            # sort the list by frequency on writing, since we have overlaps https://www.reddit.com/r/learnprogramming/comments/91bl6v/python_sort_multiple_lists_based_on_the_sorting/
            pn_x, pn_y = zip(*sorted(zip(pn_x, pn_y)))
            #pn_x_smooth, pn_y_smooth = box_smooth(pn_x, pn_y, 31)   
            pn_x_smooth = savitzky_golay(np.array(pn_x), 57, 3)
            pn_y_smooth = savitzky_golay(np.array(pn_y), 57, 3)
        
            for x, y, xs, ys in zip(pn_x, pn_y, pn_x_smooth, pn_y_smooth):
                outf.write("%d,%.2f,%.2f,%.2f\n" % (x,y,xs,ys))
                
    except Exception as e:
        sys.stderr.write("%s\n" % (e))
    finally:
        # main reason to factor code out was to run it in an exception handler and
        # reset state in case of an error
        print("restoring state")
        sa.restore_state()

if __name__ == '__main__':
    
    #noise_floor()
    #exit(0)
    
    
    ip_address = "192.168.2.199"
    gpib_address = 7
    rm = pyvisa.ResourceManager()
    rsrc = rm.open_resource("TCPIP::%s::gpib0,%d::INSTR" % (ip_address, gpib_address))
    
    sa = Tektronix2756P(rsrc)
    sa.save_state()
    
    # TODO: check RFATT, figure out MINATT and MAXPWR, although
    # it's probably too late by the time the user has plugged
    # in the cables.
    
    # lazy way to make sure we have 10 dB/div, auto resbw, etc
    sa.reset()
    
    sa.set_time_auto()
    
    # this is a key setup step of PN.EXE; I thought reset would
    # set it, but apparently I had that backwards.
    sa.set_cursor_avg()
    
    note = "HP 8640B + PDRO at 100 MHz after 2756P calibration"
    
    # should not change; always the base carrier, so we can scale it by external_multiplier
    nominal_carrier = 100e6 
    
    # this will set the starting center frequency, so should be close to accurate
    external_multiplier = 42
    retune_carrier = True
    carrier_level = -5    
    
    # this will update as we take measurements (and will be actual center frequency)
    last_nominal_carrier = nominal_carrier * external_multiplier
    
    min_offset = 100
    max_offset = 1e6
    clip = -30
    vbw = "0"
    
    runs_to_average = 4
    list_of_runs = []
    
    # pn_x is same for all runs; stash the last one here if we're averaging
    pn_x = None
    pn_y = None  
        
    try:
        for idx in range(runs_to_average):
            print("*** starting run %d of %d" % (idx + 1, runs_to_average))
            pn_x, pn_y, last_nominal_carrier = scaled_phase_noise(sa, last_nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip=clip, vbw=vbw)
            
            # scale if using external multiplier, since I got tired of doing this in DataGraph
            if external_multiplier > 1:
                multiplier_corr = - 20 * log10(last_nominal_carrier / nominal_carrier)
                sys.stderr.write("*** EXTERNAL MULTIPLIER *** adjusting by %.1f dB\n" % (multiplier_corr))
                pn_y = np.array(pn_y) + multiplier_corr
                
            list_of_runs.append(pn_y)
            
        pn_y = np.average(list_of_runs, axis=0) if runs_to_average > 1 else list_of_runs[0]
        
        output_name = "phase_noise_py.%s.csv" % (datetime.now().strftime("%Y-%m-%d %H%M"))
        with open(output_name, "w") as outf:
            
            # log RFATT before restoring state, since it depends on clip level
            outf.write("# note: %s\n# runs averaged: %d\n# nominal_carrier: %f Hz\n# measured carrier: %f Hz\n# external_multiplier: %d\n# carrier_level: %d dBm\n# retune_carrier: %d\n# min_offset: %d Hz\n# max_offset: %d Hz\n# clip: %d\n# vbw: %s\n# rfatt: %s dB\n#\n" % (note, runs_to_average, nominal_carrier, last_nominal_carrier, external_multiplier, carrier_level, retune_carrier, min_offset, max_offset, clip, vbw, sa.rfatt()))
            
            outf.write("f (Hz),ℒ (dBc/Hz),f_smooth (Hz),ℒ_smooth (dBc/Hz)\n") 

            # sort the list by frequency on writing, since we have overlaps https://www.reddit.com/r/learnprogramming/comments/91bl6v/python_sort_multiple_lists_based_on_the_sorting/
            pn_x, pn_y = zip(*sorted(zip(pn_x, pn_y)))
            #pn_x_smooth, pn_y_smooth = box_smooth(pn_x, pn_y, 31)   
            pn_x_smooth = savitzky_golay(np.array(pn_x), 51, 3)
            pn_y_smooth = savitzky_golay(np.array(pn_y), 51, 3)
        
            for x, y, xs, ys in zip(pn_x, pn_y, pn_x_smooth, pn_y_smooth):
                outf.write("%d,%.2f,%.2f,%.2f\n" % (x,y,xs,ys))
                
    except Exception as e:
        sys.stderr.write("%s\n" % (e))
    finally:
        # main reason to factor code out was to run it in an exception handler and
        # reset state in case of an error
        print("restoring state")
        sa.restore_state()
    
            
