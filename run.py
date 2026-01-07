# run_mysql.py - MySQL版快速启动脚本（优化版）
import os
import sys
import subprocess


def check_environment():
    """检查运行环境"""
    print("[检查] 检查Python环境...")
    try:
        import pymysql
        import flask
        print("[检查] 依赖包检查完成")
        return True
    except ImportError as e:
        print(f"[错误] 缺少依赖包: {e}")
        print("[安装] 正在安装依赖包...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            print("[安装] 依赖包安装完成")
            return True
        except subprocess.CalledProcessError:
            print("[错误] 依赖包安装失败")
            return False


def check_database():
    """检查MySQL连接"""
    print("[MySQL] 检查数据库连接...")
    try:
        import pymysql

        # 测试连接
        conn = pymysql.connect(
            host='localhost',
            port=3306,
            user='root',
            password='your_password',
            charset='utf8mb4'
        )
        conn.close()
        print("[MySQL] 数据库连接成功")
        return True
    except Exception as e:
        print(f"[MySQL] 连接失败: {e}")
        print("\n[提示] 请确保:")
        print("  1. MySQL服务正在运行")
        print("  2. 数据库配置正确（修改app.py中的MySQL配置）")
        print("  3. 已创建数据库和表（运行 python create_database.py）")
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("项目立项系统 - MySQL版启动器（优化版）")
    print("=" * 60)

    # 检查环境
    if not check_environment():
        print("[错误] 环境检查失败")
        sys.exit(1)

    # 检查数据库
    if not check_database():
        print("[警告] 数据库连接检查失败，尝试继续运行...")

    # 检查配置文件
    if not os.path.exists('app.py'):
        print("[错误] 找不到app.py文件")
        sys.exit(1)

    # 启动应用
    print("\n[系统] 启动项目立项系统...")
    print("[系统] 访问地址: http://localhost:5000")
    print("[系统] 默认账号: admin / admin123（部门：质控部）")
    print("[系统] 重要提示：")
    print("      1. 请先使用管理员账号添加用户")
    print("      2. 然后使用添加的用户登录")
    print("      3. 管理员账号可以多人同时登录")
    print("[系统] 按 Ctrl+C 停止服务")
    print("=" * 60)

    # 导入并运行app
    from app import app
    app.run(debug=True, host='0.0.0.0', port=5000)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[系统] 服务已停止")
    except Exception as e:
        print(f"[错误] 启动失败: {e}")