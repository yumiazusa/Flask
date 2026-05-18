# run.py - 生产友好的启动器：父进程监控子进程内存并自动重启
import os
import signal
import subprocess
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None


APP_CHILD_FLAG = '--child'
DEFAULT_MEMORY_LIMIT_MB = int(os.environ.get('APP_MEMORY_LIMIT_MB', '2300'))
CHECK_INTERVAL_SECONDS = int(os.environ.get('APP_MEMORY_CHECK_INTERVAL', '15'))
MAX_CONSECUTIVE_BREACHES = int(os.environ.get('APP_MEMORY_BREACH_COUNT', '2'))


def check_environment():
    """检查基础依赖是否可用。"""
    print("[检查] 检查Python环境...")
    try:
        import flask  # noqa: F401
        import pymysql  # noqa: F401
        print("[检查] 依赖包检查完成")
        return True
    except ImportError as e:
        print(f"[错误] 缺少依赖包: {e}")
        requirement_file = 'requirements' if os.path.exists('requirements') else 'requirements.txt'
        print(f"[安装] 正在安装依赖包: {requirement_file}")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", requirement_file])
            print("[安装] 依赖包安装完成")
            return True
        except subprocess.CalledProcessError:
            print("[错误] 依赖包安装失败")
            return False


def run_child():
    """子进程中启动Flask服务。"""
    from app import app, check_database, config

    print("[子进程] 正在检查数据库...")
    success, message = check_database()
    if not success:
        print(f"[子进程] 数据库检查失败: {message}")
        return 1

    print(f"[子进程] 启动服务: http://{config.HOST}:{config.PORT}")
    app.run(
        debug=False,
        host=config.HOST,
        port=config.PORT,
        threaded=True,
        use_reloader=False
    )
    return 0


def start_child_process():
    """由父进程拉起服务子进程。"""
    command = [sys.executable, os.path.abspath(__file__), APP_CHILD_FLAG]
    return subprocess.Popen(command, cwd=os.path.dirname(os.path.abspath(__file__)))


def terminate_process(process):
    """优先温和终止，超时后强制结束。"""
    if process.poll() is not None:
        return

    try:
        if os.name == 'nt':
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def monitor_child_process():
    """持续监控服务进程RSS，超阈值时自动重启。"""
    if psutil is None:
        print("[警告] 未安装 psutil，无法启用内存监控，当前仅启动服务。")
        child = start_child_process()
        return child.wait()

    print("=" * 60)
    print("项目立项系统 - 内存守护启动器")
    print("=" * 60)
    print(f"[监控] 内存上限: {DEFAULT_MEMORY_LIMIT_MB} MB")
    print(f"[监控] 检查间隔: {CHECK_INTERVAL_SECONDS} 秒")
    print(f"[监控] 连续超限次数: {MAX_CONSECUTIVE_BREACHES}")
    print("[系统] 按 Ctrl+C 停止服务")
    print("=" * 60)

    child = start_child_process()
    consecutive_breaches = 0

    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)

        if child.poll() is not None:
            print(f"[监控] 子进程已退出，退出码: {child.returncode}，准备重启")
            child = start_child_process()
            consecutive_breaches = 0
            continue

        try:
            rss_mb = round(psutil.Process(child.pid).memory_info().rss / (1024 * 1024), 2)
        except psutil.Error as e:
            print(f"[监控] 读取子进程内存失败: {e}")
            continue

        print(f"[监控] 子进程 PID={child.pid} 当前RSS={rss_mb} MB")

        if rss_mb >= DEFAULT_MEMORY_LIMIT_MB:
            consecutive_breaches += 1
            print(f"[监控] 内存超限，第 {consecutive_breaches} 次触发阈值")
        else:
            consecutive_breaches = 0

        if consecutive_breaches >= MAX_CONSECUTIVE_BREACHES:
            print("[监控] 触发自动优雅重启，准备回收异常内存占用")
            terminate_process(child)
            child = start_child_process()
            consecutive_breaches = 0


def main():
    """入口函数。"""
    if APP_CHILD_FLAG in sys.argv:
        return run_child()

    if not check_environment():
        print("[错误] 环境检查失败")
        return 1

    if not os.path.exists('app.py'):
        print("[错误] 找不到app.py文件")
        return 1

    return monitor_child_process()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[系统] 服务已停止")
    except Exception as e:
        print(f"[错误] 启动失败: {e}")
        sys.exit(1)
