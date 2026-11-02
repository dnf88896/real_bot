import base64,json,tempfile,unittest
from pathlib import Path
import image_library as lib
from mouth import build_request,parse_output

class ImageTest(unittest.TestCase):
    def test_paths_offered_images_and_no_pixels_in_api(self):
        with tempfile.TemporaryDirectory() as d:
            old=lib.LIBRARY;lib.LIBRARY=Path(d)/'images';lib.LIBRARY.mkdir()
            try:
                data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jJesAAAAASUVORK5CYII=')
                p=lib.LIBRARY/'test.png';p.write_bytes(data)
                offered=[{'path':str(p),'description':'一张测试图片','kind':'sticker'}]
                item={'id':1,'observation':{'account_id':'1','chat_type':'private','conversation_id':'2','raw_event':{'message':[]}}}
                packet={'status':'awaiting_mouth','scene':{'platform':'qq','account':'1','chat_type':'private','conversation':'2'},'new_messages':[item],'related_memories':[],'image_options':offered}
                request=build_request(packet,'自然交流')
                self.assertNotIn('base64',json.dumps(request))
                context=json.loads(request['messages'][1]['content'])
                self.assertEqual(context['可用表情包'],[{'名称':'test','说明':'一张测试图片'}])
                self.assertNotIn('available_images',context)
                self.assertNotIn('仅输出JSON',request['messages'][0]['content'])
                text,paths=parse_output(json.dumps({'text':'给你','images':[str(p)]}),packet)
                self.assertEqual(text,'给你');self.assertEqual(paths,[str(p)])
                self.assertEqual(base64.b64decode(lib.segments_for(paths,offered)[0]['data']['file'][9:]),data)
                other=Path(d)/'outside.png';other.write_bytes(data)
                with self.assertRaises(ValueError):lib.checked_image(str(other))
                bad=lib.LIBRARY/'bad.png';bad.write_text('secret text')
                with self.assertRaises(ValueError):lib.checked_image(str(bad))
                with self.assertRaises(ValueError):lib.select_images([str(p)],[])
                with self.assertRaises(ValueError):lib.select_images([str(p)]*3,offered)
                with self.assertRaises(ValueError):parse_output(json.dumps({'text':'x','images':[str(other)]}),packet)
                packet['scene']['chat_type']='group';item['observation']['chat_type']='group'
                self.assertEqual(json.loads(build_request(packet,'自然交流')['messages'][1]['content'])['scene']['category'],'qq_group')
            finally:lib.LIBRARY=old

if __name__=='__main__':unittest.main()
