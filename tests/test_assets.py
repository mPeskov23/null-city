import unittest
from server.app import Handler, ApiError

class AssetTests(unittest.TestCase):
    def test_svg_assets_have_correct_mime_and_traversal_is_denied(self):
        handler=Handler.__new__(Handler)
        results=[]
        handler.respond=lambda status,body,mime:results.append((status,body,mime))
        for asset in ['bg-room-svg','char-01-desert','char-10-hive']:
            handler.static('/assets/nulls/'+asset+'.svg')
            status,body,mime=results[-1]
            self.assertEqual(status,200)
            self.assertTrue(mime.startswith('image/svg+xml'))
            self.assertTrue(body.startswith(b'<svg'))
        with self.assertRaises(ApiError):handler.static('/assets/nulls/../../server/app.py')

    def test_pwa_assets_and_manifest_served_with_correct_mime(self):
        handler=Handler.__new__(Handler)
        results=[]
        handler.respond=lambda status,body,*args,**kwargs: results.append((status,body,args,kwargs))
        for path,expected_mime in [
            ('/manifest.webmanifest','application/manifest+json'),
            ('/sw.js','text/javascript'),
            ('/src/pwa.js','text/javascript'),
            ('/assets/icon-192.png','image/png')
        ]:
            handler.static(path)
            status,body,args,kwargs=results[-1]
            mime=kwargs.get('mime') or (args[1] if len(args)>1 else (args[0] if len(args)>0 else ''))
            self.assertEqual(status,200)
            self.assertTrue(mime.startswith(expected_mime),f"{path} had unexpected mime {mime}")
