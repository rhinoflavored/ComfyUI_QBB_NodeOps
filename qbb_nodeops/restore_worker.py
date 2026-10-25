"""One-shot offline worker. Never imports ComfyUI or the plugin startup module."""
import argparse
import importlib
import os
from pathlib import Path
import sys
import time
import types


def modules():
    package = types.ModuleType('_nodeops_restore_worker')
    package.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + '.current_restore')


def work(job, plan_hash, *, handoff_timeout=30, exit_timeout=120):
    restore = modules()
    manager = Path(__file__).resolve().parent.parent
    job = Path(job).resolve()
    if restore.job_path(job.name, manager) != job:
        raise ValueError('Worker task is not under this protected manager')
    with restore.restore_process.task_lock(job / 'worker.lock'):
        value = restore.frozen(job, plan_hash)
        runtime = value['runtime']
        if restore.status(job.name, manager)['status'] != 'starting':
            raise ValueError('Task not authorized for worker startup')
        if restore.restore_process.identity(runtime['parent_pid']) != runtime['parent_birth']:
            raise ValueError('Original process already gone before handoff; refusing implicit execution')
        restore.update(job, worker_pid=os.getpid(), worker_birth=restore.restore_process.identity(os.getpid()))
        restore.atomic_json(job / 'worker-ready.json', {'pid': os.getpid(), 'plan_hash': plan_hash})
        deadline = time.monotonic() + handoff_timeout
        while not (job / 'handoff.json').exists():
            if restore.status(job.name, manager)['status'] != 'starting':
                return 2
            if time.monotonic() >= deadline:
                restore.update(job, status='handoff_failed', error='No explicit handoff received; no changes made')
                return 2
            time.sleep(0.05)
        handoff = restore.read_json(job / 'handoff.json')
        if handoff != {'plan_hash': plan_hash, 'parent_birth': runtime['parent_birth']}:
            raise ValueError('Handoff identity mismatch')
        restore.update(job, status='waiting_for_exit', manual_restart=True, message='Close the original ComfyUI window manually. Wait for this restore window to finish before restarting.')
        print('请手动关闭原 ComfyUI 启动窗口；不要关闭本恢复窗口。恢复完成前不要重新启动 ComfyUI。', flush=True)
        print('Close the original ComfyUI manually. Keep this restore window open and wait for completion before restarting.', flush=True)
        deadline = time.monotonic() + exit_timeout
        while restore.restore_process.identity(runtime['parent_pid']) == runtime['parent_birth']:
            if restore.status(job.name, manager)['status'] != 'waiting_for_exit':
                return 2  # Controlled stop was refused/cancelled by the server.
            if time.monotonic() >= deadline:
                restore.update(job, status='handoff_failed', error='Original process did not exit; no changes made')
                return 2
            time.sleep(0.1)
        if restore.status(job.name, manager)['status'] != 'waiting_for_exit':
            return 2
        try:
            value = restore.frozen(job, plan_hash)
            print('原 ComfyUI 已关闭，正在执行确认的恢复计划。请等待完成提示。', flush=True)
            print('Original ComfyUI closed. Applying the approved restore plan; please wait.', flush=True)
            outcome = restore.execute(job, value)
        except Exception as exc:
            previous = restore.status(job.name, manager)
            if previous['status'] != 'failed_rollback':
                restore.update(job, status='interrupted', error=str(exc), message='No success claimed; inspect retained task journal')
            print('恢复未完成，请检查日志：' + str(exc), flush=True)
            return 3
        # Never spawn ComfyUI. The user starts it only after offline work completes.
        if outcome == 'applied':
            restore.update(job, status='awaiting_manual_restart', outcome=outcome, manual_restart=True,
                message='Restore actions applied. Manually start ComfyUI with your usual launcher; no automatic restart.')
            print('恢复操作已完成。现在可以使用原来的启动脚本手动启动 ComfyUI；本程序不会自动启动服务。', flush=True)
            print('Restore actions applied. Start ComfyUI manually using your usual launcher. No automatic restart.', flush=True)
        else:
            restore.update(job, status='failed_rolled_back', outcome=outcome, manual_restart=True,
                message='Restore failed and rolled back. No automatic restart; inspect worker.log.')
            print('恢复失败并已回退。请检查 worker.log；未自动启动 ComfyUI。', flush=True)

    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--plan-hash', required=True)
    parser.add_argument('--manual-lifecycle', action='store_true')
    args = parser.parse_args()
    if args.manual_lifecycle:
        if os.name == 'nt':
            # Rebind to the NEW console, never the original terminal's handles.
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleTitleW('QBB NodeOps - 离线恢复：完成后请手动启动 ComfyUI')
            sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
            sys.stderr = sys.stdout
            sys.stdin = open('CONIN$', 'r', encoding='utf-8')
        log = (args.job / 'worker.log').open('a', encoding='utf-8', buffering=1)
        class Tee:
            def __init__(self, stream): self.stream = stream
            def write(self, text):
                self.stream.write(text); log.write(text)
            def flush(self):
                self.stream.flush(); log.flush()
        sys.stdout, sys.stderr = Tee(sys.stdout), Tee(sys.stderr)
    try:
        result = work(args.job, args.plan_hash, exit_timeout=1800)
        return result
    except Exception as exc:
        print('Restore worker refused: ' + str(exc), flush=True)
        return 1
    finally:
        if args.manual_lifecycle and os.name == 'nt':
            print('请查看以上结果。按任意键关闭恢复窗口。 / Press any key to close this restore window.', flush=True)
            try:
                import msvcrt
                msvcrt.getwch()
            except OSError:
                pass


if __name__ == '__main__':
    sys.exit(main())
