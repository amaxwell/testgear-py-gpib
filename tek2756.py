#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14

#
# Requires pyvisa, and I'm using pyvisa-py to connect to my SA over ethernet
# using a Tek AD007 GPIB-ethernet bridge. I haven't tried to make it work
# with AR488, as part of the motivation for this is to avoid the timeout
# limitations of AR488, especially when used with John Miles' phase noise
# program.
#
# 
#

import pyvisa
import time
from struct import *

#pyvisa.log_to_screen()

class Filter(object):
    """docstring for Filter"""
    def __init__(self, frequency, filter_list):
        super(Filter, self).__init__()
        self.frequency = frequency
        self.frequency_error = float(filter_list[0])
        self.frequency_cal_code = int(filter_list[1])
        
        self.level_error = float(filter_list[2])
        self.level_cal_code = int(filter_list[3])
        
        # Filter object: 3000000 Hz 61.9 dBm
        # Filter object: 1000000 Hz 57.5 dBm
        # Filter object: 100000 Hz 47.3 dBm
        # Filter object: 10000 Hz 37.4 dBm
        # Filter object: 1000 Hz 27.1 dBm
        # Filter object: 100 Hz 17.3 dBm
        # Filter object: 10 Hz 6.3 dBm
        
        self.noise_bandwidth_F = float(filter_list[4])
        self.noise_bandwidth_cal_code = int(filter_list[5])
    
    def __repr__(self):
        return "Filter object: %s Hz" % (self.frequency)


class Tektronix2756P(object):
    
    def __init__(self, ip_address="192.168.2.199", gpib_address=7):
        super(Tektronix2756P, self).__init__()
        self._state = []
        self._filters = None
        
        rm = pyvisa.ResourceManager()
        self._tek2756 = rm.open_resource("TCPIP::%s::gpib0,%d::INSTR" % (ip_address, gpib_address))
        self._tek2756.timeout = None
        
        # shouldn't use termchar since the rear switches are set to use EOI
        #
        #tek2756.set_visa_attribute(pyvisa.constants.VI_ATTR_TERMCHAR_EN, True)
        #tek2756.set_visa_attribute(pyvisa.constants.VI_ATTR_TERMCHAR, 10)
        #tek2756.read_termination = "\n"
        #tek2756.write_termination = "\n"

    def save_state(self):
        """Pushes the current state onto the stack"""
        #FINE OFF;DELFR OFF;RESBW AUTO;MARKER OFF;FRQRNG 1;EXMXR OFF;MINATT 10;RLMODE MNOISE;RLUNIT DBM;ROFSET +0.0;REFLVL -20.0;FINE OFF;VRTDSP LOG:10;FIRST +2.171946963E+9;FREQ +1.0E+8;DELFR OFF;SPAN +1.0E+6;ZEROSP OFF;MXSPN OFF;IDENT OFF;RESBW AUTO;PEAK KNOB;TIME AUTO;TRIG FRERUN;AVIEW ON;BVIEW ON;SAVEA OFF;BMINA OFF;MXHLD OFF;CRSOR KNOB;PLSTR OFF;VIDFLT OFF;REDOUT ON;GRAT ON;CLIP OFF;DISCOR OFF;COUNT OFF;CRES +1.0E+0;WFMPRE WFID:A,ENCDG:BIN;POINT 500,225;RQS ON;EOS OFF;DT ON;NUMEV 0;SGERR OFF;HDR ON;SGTRAK OFF;MCPOIN OFF;NSELVL OFF;MTRACE PRIMAR:NONE;MTRACE SECOND:NONE;TMODE FREQ;STYPE CW;STEP +1.0E+8;THRHLD AUTO;BWNUM +6.0;BWMODE OFF;TGMODE OFF;SAMODE OFF;WARMSG ON;RGMODE OFF;ECR OFF;ZETIME OFF;GSL OFF
        self._state.append(self._tek2756.query("SET?").strip())
        
    def restore_state(self):
        """Pops the last state from the stack and sends it to the analyzer"""
        assert len(self._state), "state has not been set"
        self._tek2756.write(self._state.pop())
        
    def reset(self):
        self._tek2756.write("INIT")
        
    def reflevel(self):
        # REFLVL -19.5\r\n
        return float(self._tek2756.query("REFLVL?").strip().split()[-1])
        
    def set_reflevel(self, reflevel):
        self._tek2756.write("REFLVL %s" % (reflevel))
        
    def set_center_frequency(self, freq, units="HZ"):
        self._tek2756.write("FREQ %f %s" % (float(freq), units))
        
    def center_frequency_hz(self):
        return float(self._tek2756.query("FREQ?").strip())
        
    def span(self):
        return self._tek2756.query("SPAN?").strip()
        
    def set_span(self, span, units="HZ"):
        self._tek2756.write("SPAN %s %s" % (int(span), units))

    def rbw(self):
        return self._tek2756.query("RESBW?").strip()
        
    def set_rbw(self, rbw="AUTO"):
        self._tek2756.write("RESBW %s" % (rbw))
        
    def vbw(self):
        return self._tek2756.query("VIDFLT?").strip()
        
    def set_vbw(self, vbw="OFF"):
        assert vbw in ("0", "OFF", "NARROW", "WIDE"), "invalid video bandwidth"
        self._tek2756.write("VIDFLT %s" % (vbw))
        
    def enable_single_sweep(self):
        # need to turn this on before we start using it with WAIT
        self._tek2756.write("SIGSWP")
        
    def rfatt(self):
        return self._tek2756.query("RFATT?").strip()
        
    def filter(self, frequency):
        
        if self._filters is None:
            self._filters = {}
            cals = self._tek2756.query("CAL?").strip("CAL \r\n").split(",")

            for x, f in zip(range(0, len(cals), 6), (3e6, 1e6, 100e3, 10e3, 1e3, 100, 10)):
                filt = Filter(int(f), cals[x:x+6])
                self._filters[int(f)] = filt

        return self._filters[frequency]
        
    def curve(self):
        self._tek2756.write("WFMPRE ENC:BIN")
        self._tek2756.write("WFMPRE WFID:FULL")
                
        # 
        # Took a while to figure out that I needed a blocking call in the same
        # line with the WAIT, and also needed to adjust the pyvisa timeout. 
        #
        # get the preamble and turn it into a dictionary
        header_data = self._tek2756.query("SIGSWP;SIGSWP;WAIT;WFMPRE?")
        header_dict = {}
        for header_pair in header_data.split(","):
            key, value = header_pair.strip().split(":")
            header_dict[key] = value
        
        self._tek2756.write("CURVE?")
        waveform_data = self._tek2756.read_raw(10)
        # b'CURVE CRVID:FULL,%\x03\xe9BLFCJEADCAELFG>FHFF=?CKDDA?CJG<=G=MJFGAG@AG;BHK<ADFAMI?@F>CFAFEAGADD@DDGJIKBHCA>?K>KC@<??C?EDEFFF@BHEHDHE?I8=CFELHGF?BCL<OLBIHHFDEGHJCCIMAFDCAJCCACI<ELGFGGE@HGCCDG=A;B@LBC8IGBKAFCKD<?CFLKD<EGFGHB@H=BLIFDBLFHCAEIDGEDA=HFGFEFKBCEBC;AADECEGB>GGLKE?>FB<>IGC?ACD?>C@HADGKDH@F>A<FGGKC;=JHCA>FCFFJIFAGEGMGFDG@QFDGIEHF?:GHFBDGKGGHKG<C>GBDA@AEFDBGCHCEF<D?JFAF?G@GB@DD@CCCDDCDA9@CHG@FBF:>JDI@FBBIG:CAC<KCAABCG@LBF?BF>F?I?LFFDGAGD?BFGCK6=GL@NCAGCAAHBHHCBM9GG9FPGOFDLGHFNILLQPUUYX]\\``bdghlnpsxy|\x7f\x83\x86\x8a\x8e\x92\x96\x9a\x9e\xa3\xa7\xac\xb2\xb8\xbc\xc2\xc7\xcc\xd1\xd5\xd8\xdb\xdd\xde\xde\xde\xdc\xdb\xd8\xd4\xcf\xc9\xc4\xbe\xb8\xb4\xaf\xaa\xa4\x9f\x9b\x96\x91\x8d\x88\x84\x80}{vroljgde`__XXWTTOPVOMOEH>ILMGEHCCCKDCFFHEFMH?EHH?IFFEG;GFBHC?C@?CAH>@GGBCBL:DFIFC@AGHGHAEEGFFJ>@GFFGECEEFDDBGAF4DJ?EIDAC@FDA?C<B@@GGCCF?NC?BAB8F@BD<BEG>@JB>GDDFE?FAG=G;BHCDI?FMF?GHEA;>FJLDB@DJBHG>JG@CFJILCFGC?AHFFEFEDICHGGAJDACABDEDFEDGLLBK@GD<PJD>CAGFGF@9D?FHID>CAA@CD?BHDGHCIHICCBBEHGCC<H??HKGIDCFH?CE@CGG@EG?FE?AGICCGF???@9FHGAD>AE?DH?<BDLDNA=F?ACFD>JFD5ND?JE=ACGCED@?F@G=;L@=<HDDCAEHDBHJDCJEDGAJ@EGD=H???BFCGIHEFFGIDK>FFIBDF?AFCBB>=DAF@?MCL@EHFCBC>L;?KDADGHGG9H?DK\x89\r\n'
        # 1023 bytes
        
        # String at the front is basically junk; the length depends
        # on position of the %. If requesting a FULL curve, it's 18, but
        # would be less for just A or B.
        a = unpack(">18s2B1000B1B2s", waveform_data)

        # Had to look at KE5FX's pn.cpp to figure out the packing, as the
        # documentation is ambiguous on the position of the checksum. Reading
        # the length as two bytes is easier for later checksum computation
        # instead of reading it as a big-endian uint16_t
        msb = a[1]
        lsb = a[2]
        binary_array_count = 256 * msb + lsb
        
        checksum = a[-2]
        checksum += msb
        checksum += lsb
        
        # this slice is the actual spectral data
        spectral_values = a[3:-2]
        for a in spectral_values:
            checksum += a
            checksum = checksum % 256
            
        assert checksum == 0, "invalid checksum from binary curve"
        xincr = float(header_dict["XINCR"])
        xzero = float(header_dict["XZERO"])
        pt_off = float(header_dict["PT.OFF"])
        #print("xincr, xzero, pt_off", xincr, xzero, pt_off)

        yoff = float(header_dict["YOFF"])
        ymult = float(header_dict["YMULT"])
        yzero = float(header_dict["YZERO"])
        print("yoff, ymult, yzero", yoff, ymult, yzero)
        
        scaled_x = []
        scaled_y = []
        
        for i, y in enumerate(spectral_values):
            scaled_x.append((i - pt_off) * xincr + xzero)
            scaled_y.append(yzero + (y - yoff) * ymult)

        return (scaled_x, scaled_y)
        
    def carrier_near(self, frequency):
        """Returns two-tuple of frequency and level of a carrier near the starting frequency
        Should be reasonably safe to call, as it will save and restore state.
         """
        self.save_state()
        
        self.set_center_frequency(frequency)
        self.set_reflevel("+30 DBM")
        self.set_span(10e3)
        self.set_rbw("AUTO")
        
        # COUNT OFF,+9.9999995E+7
        # zooming in on this doesn't improve the count
        count_desc = self._tek2756.query("SIGSWP;SIGSWP;WAIT;FIBIG;TOPSIG;COUNT;COUNT?")
        level = self.reflevel()
        count = float(count_desc.split(",")[-1])
        
        self.restore_state()
        return (count, level)
        


if __name__ == '__main__':
        
    sa = Tektronix2756P()
    sa.save_state()
    
    sa.set_center_frequency(100, "MHZ")
    
    min_offset = 100
    assert min_offset >= 100, "starting offset from carrier must be at least 100 Hz"
    max_offset = 1e6
    nominal_carrier = 100e6
    carrier = nominal_carrier
    
    carrier, carrier_level = sa.carrier_near(carrier)
    print("found carrier:", carrier, carrier_level)

                
    sa.restore_state()
