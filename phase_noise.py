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

from tek2756 import Tektronix2756P        

def scaled_phase_noise(sa, nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip=0, vbw="0"):
    
    pn_x = []
    pn_y = []
    
    carrier = nominal_carrier

    # center frequency; we start measuring at the offset, not at the peak,
    # so we tune to the right of the carrier
    tune_freq = carrier + min_offset
    
    # FIXME: use measured?
    sa.set_reflevel(carrier_level)
    sa.set_vbw(vbw)
    
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
           
        print("xxxxxxxxxxxxxxx")

        # 100-1,000, 1,000-10,000, 10,000-100,000
        # decade_start is our offset from the carrier, and it will be a decade wide
        decade_start = int(pow(10.0, current_decade) + 0.5)        
        total_span = decade_start * 10
    
        rbw = int(decade_start / 10)
        sa.set_rbw(rbw)
   
        print("decade %s Hz to %s Hz offset" % (decade_start, decade_start + total_span))
        print("RBW: %s Hz, Span/Div: %s Hz" % (rbw, total_span / 10))
    
        # set_span requires span/div
        sa.set_span(total_span / 10, units="HZ")
    
        # jog frequency by half the total span (starts at carrier + decade_start)
        # check carrier and retune each decade in case of slow drift
        if retune_carrier:
            carrier, measured_carrier_level = sa.carrier_near(carrier)
        
            # hit this with the HP 8620C; drifted so far I couldn't find it
            assert abs(abs(carrier_level) - abs(measured_carrier_level)) < 10, "*** ERROR *** no carrier detected within 10 dB of nominal value"
            
            # warn in case of drift; tried 10 Hz and hit that immediately with
            # an old Tek 067-0532-00 leveled sine generator
            if abs(carrier - nominal_carrier) > total_span / 4:
                sys.stderr.write("*** WARNING *** Carrier drifted more than %d Hz from nominal: %d\n" % (total_span/4, carrier))
        
        center_frequency = carrier + decade_start + total_span/2
        sa.set_center_frequency(center_frequency, units="HZ")
        print("center at %s for carrier at %s" % (center_frequency, carrier))
    
        scaled_x, scaled_y = sa.curve()
    
        # HP uses -10 * log10(1.2 * rbw) as starting estimate. KE5FX uses an additional
        # additive factor, but not the 1.2. This just confused me, so I pull the cal
        # constants for this machine and use them directly.

        # Need to subtract this value of noise bandwidth correction (in dB, per manual),
        # which matches the HP PN seminar notes. Had the sign wrong here initially and
        # got super confused because the values are so much larger than the ones in pn.cpp.
        f_corr = -1 * sa.filter(rbw).noise_bandwidth_F
        print("F corr dB:", f_corr)
    
        # FIXME check this; HP default value. Probably reasonable, given how close
        # the -10 * log10(1.2 * rbw) value is to our actual constants. KE5FX says
        # the Tek SA's track this internally, so it should be zero.
        Cn = 0
            
        # Extend the previous results; sort at the end because of overlap.
        # Compute frequency offset relative to most recently measured carrier
        # in case retune_carrier is true.
        for sx, sy in zip(scaled_x, scaled_y):
            pn_x.append(sx - carrier)
            pn_y.append(sy - carrier_level + f_corr + Cn)
    
    return pn_x, pn_y

if __name__ == '__main__':
    
    from math import log10, pow
    import sys
    from datetime import datetime
    
    sa = Tektronix2756P()
    sa.save_state()
    
    # TODO: check RFATT, figure out MINATT and MAXPWR, although
    # it's probably too late by the time the user has plugged
    # in the cables.
    # set up 10 dB/div
    
    note = "HP 8663A + CTI PDRO oscillator at 4.3 GHz and 100 MHz IF"
    nominal_carrier = 4300e6
    carrier_level = -5
    retune_carrier = True
    min_offset = 100
    max_offset = 1e6
    clip = -30
    vbw = "0"
        
    try:
        pn_x, pn_y = scaled_phase_noise(sa, nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip=clip, vbw=vbw)
        output_name = "phase_noise_py.%s.csv" % (datetime.now().strftime("%Y-%m-%d %H%M"))
        with open(output_name, "w") as outf:
            
            # log RFATT before restoring state, since it depends on clip level
            outf.write("# note: %s\n# nominal_carrier: %f Hz\n# carrier_level: %d dBm\n# retune_carrier: %d\n# min_offset: %d Hz\n# max_offset: %d Hz\n# clip: %d\n# vbw: %s\n# rfatt: %s dB\n#\n" % (note, nominal_carrier, carrier_level, retune_carrier, min_offset, max_offset, clip, vbw, sa.rfatt()))
            
            outf.write("f (Hz),ℒ (dBc/Hz)\n")    
        
            # sort the list by frequency on writing, since they are
            # overlapping
            # https://www.reddit.com/r/learnprogramming/comments/91bl6v/python_sort_multiple_lists_based_on_the_sorting/
         
            for x, y in sorted(zip(pn_x, pn_y)):
                outf.write("%d,%.2f\n" % (x,y))
                
    except Exception as e:
        sys.stderr.write("%s\n" % (e))
    finally:
        # main reason to factor code out was to run it in an exception handler and
        # reset state in case of an error
        print("restoring state")
        sa.restore_state()
    
            
