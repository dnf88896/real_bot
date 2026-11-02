import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import mouth
import incoming_images
import image_library as lib

class ImageRolesTest(unittest.TestCase):
    def test_sticker_palette_is_independent_of_brain(self):
        packet={'scene':{'platform':'qq','chat_type':'private'},'image_options':[{'path':'brain-selected'}]}
        palette=[{'path':'library-path','description':'比心','kind':'sticker'}]
        with patch.object(mouth,'catalog',return_value=palette):
            prepared=mouth.expression_packet(packet)
        self.assertEqual(prepared['image_options'],palette)
        self.assertEqual(packet['image_options'][0]['path'],'brain-selected')

    def test_incoming_reference_is_forwarded_without_pixels(self):
        record={'id':7,'observation':{'raw_event':{'message':[{'type':'image','data':{'url':'https://example.invalid/file'}}]}}}
        with patch.object(incoming_images,'fetch',return_value='/cached/external.jpg'):
            images=incoming_images.references([record])
        self.assertEqual(images[0]['path'],'/cached/external.jpg')
        self.assertEqual(images[0]['kind'],'incoming')
        self.assertNotIn('url',images[0]);self.assertNotIn('base64',str(images))

    def test_external_cache_path_must_be_in_incoming_directory(self):
        with tempfile.TemporaryDirectory() as d:
            old=lib.LIBRARY;lib.LIBRARY=Path(d)
            try:
                p=Path(d)/'sticker.jpg';p.write_bytes(b'\xff\xd8\xfftest')
                packet={'scene':{'platform':'qq','chat_type':'private'},'incoming_images':[{'status':'available','path':str(p)}]}
                with patch.object(mouth,'catalog',return_value=[]):
                    with self.assertRaises(ValueError):mouth.expression_packet(packet)
            finally:lib.LIBRARY=old

    def test_untrusted_download_destination_rejected(self):
        for url in ('file:///etc/passwd','http://127.0.0.1/a','https://qq.com.evil.test/a'):
            with self.assertRaises(ValueError):incoming_images.validate_url(url)

if __name__=='__main__':unittest.main()
