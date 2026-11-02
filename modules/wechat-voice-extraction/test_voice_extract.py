import unittest
from extract_voice import silk_packets

class Packets(unittest.TestCase):
    def test_complete_packet_stream(self):
        self.assertEqual(silk_packets(b'\x02#!SILK_V3\x03\x00abc\x02\x00de'),2)
    def test_truncation_is_rejected(self):
        for body in (b'\x03\x00ab',b'\x03',b'\x00\x00'):
            with self.assertRaises(ValueError):silk_packets(b'\x02#!SILK_V3'+body)
    def test_wrong_format_or_no_packets(self):
        for value in (b'not audio',b'\x02#!SILK_V3'):
            with self.assertRaises(ValueError):silk_packets(value)

if __name__=='__main__':unittest.main()
