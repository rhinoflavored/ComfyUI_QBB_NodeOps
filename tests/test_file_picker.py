import importlib.util
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'qbb_nodeops'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

picker=load('folder_picker')


class FilePickerTests(unittest.TestCase):
    def test_types_unicode_and_no_child_process(self):
        import subprocess
        for kind in ('folder','zip-open','zip-save'):
            with self.subTest(kind=kind),patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose',return_value={'path':'E:/备份/文件.zip'}) as native,patch.object(subprocess,'run',side_effect=AssertionError('No child process')):
                self.assertEqual(picker.choose(kind),{'path':'E:/备份/文件.zip'})
                native.assert_called_once_with(kind)

    def test_cancel_and_lock_release(self):
        with patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose',return_value={'path':''}):
            self.assertEqual(picker.choose(),{'path':''})
        self.assertTrue(picker.LOCK.acquire(blocking=False));picker.LOCK.release()

    def test_actual_error_is_preserved(self):
        with patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose',side_effect=OSError('HRESULT 0x80070005')):
            with self.assertRaisesRegex(ValueError,'0x80070005') as error:picker.choose()
        self.assertNotIn('Windows 阻止',str(error.exception))
        self.assertTrue(picker.LOCK.acquire(blocking=False));picker.LOCK.release()

    def test_invalid_kind(self):
        with patch.object(picker,'_native_choose') as native:
            with self.assertRaises(ValueError):picker.choose('folder;code')
            native.assert_not_called()

    def test_busy_does_not_open_second_dialog(self):
        picker.LOCK.acquire()
        try:
            with patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose') as native:
                with self.assertRaisesRegex(ValueError,'已打开'):picker.choose()
                native.assert_not_called()
        finally:picker.LOCK.release()

    def test_fresh_thread(self):
        caller=threading.get_ident();seen=[]
        def native(kind):
            seen.append(threading.get_ident());return {'path':''}
        with patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose',side_effect=native):
            picker.choose()
        self.assertNotEqual(seen[0],caller)

    @unittest.skipUnless(os.name=='nt','Windows COM smoke test')
    def test_real_dialog_creation_and_configuration_without_showing_ui(self):
        native=load('native_picker')
        def check(kind):
            with patch.object(native,'_show',return_value=0x800704C7):
                return native.choose(kind)
        with patch.object(picker,'_native_choose',side_effect=check):
            for kind in ('folder','zip-open','zip-save'):
                self.assertEqual(picker.choose(kind),{'path':''})

    def test_thread_start_failure_unlocks(self):
        with patch.object(picker.os,'name','nt'),patch.object(picker.threading.Thread,'start',side_effect=RuntimeError('start failed')):
            with self.assertRaises(RuntimeError):picker.choose()
        self.assertTrue(picker.LOCK.acquire(blocking=False));picker.LOCK.release()

    def test_timeout_does_not_allow_duplicate_dialog(self):
        release=threading.Event()
        def native(kind):
            release.wait(5);return {'path':''}
        try:
            with patch.object(picker.os,'name','nt'),patch.object(picker,'_native_choose',side_effect=native),patch.object(picker.threading.Thread,'join'):
                with self.assertRaisesRegex(ValueError,'尚未完成'):picker.choose()
                self.assertTrue(picker.LOCK.locked())
                with self.assertRaisesRegex(ValueError,'已打开'):picker.choose()
        finally:
            release.set()
            for worker in threading.enumerate():
                if worker.name=='NodeOps-file-picker':worker.join(5)
        self.assertFalse(picker.LOCK.locked())
