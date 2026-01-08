# create_database.py - MySQL数据库初始化脚本（优化版）
import pymysql


def create_database():
    """创建数据库和表"""
    # MySQL连接配置（根据实际情况修改）
    config = {
        'host': '47.108.254.13',
        'port': 3306,
        'user': 'ProjectDB',
        'password': '4100282Ly@',
        'charset': 'utf8mb4'
    }

    try:
        # 连接MySQL（不指定数据库）
        conn = pymysql.connect(**config)
        cursor = conn.cursor()

        print("[MySQL] 正在创建数据库和表...")

        # 创建数据库
        cursor.execute(
            "CREATE DATABASE IF NOT EXISTS ProjectDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print("[MySQL] 数据库创建完成")

        # 使用数据库
        cursor.execute("USE ProjectDB")

        # 创建用户表
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS users
                       (
                           id
                           INT
                           AUTO_INCREMENT
                           PRIMARY
                           KEY,
                           username
                           VARCHAR
                       (
                           50
                       ) UNIQUE NOT NULL,
                           password VARCHAR
                       (
                           100
                       ) NOT NULL,
                           realname VARCHAR
                       (
                           50
                       ),
                           department VARCHAR
                       (
                           100
                       ),
                           created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci
                       """)
        print("[MySQL] 用户表创建完成")

        # 创建项目表（优化版）
        cursor.execute("""
                       CREATE TABLE IF NOT EXISTS projects
                       (
                           id
                           INT
                           AUTO_INCREMENT
                           PRIMARY
                           KEY,
                           project_no
                           VARCHAR
                       (
                           50
                       ) UNIQUE NOT NULL COMMENT '项目号，如：2026AAP0001',
                           project_name VARCHAR
                       (
                           200
                       ) NOT NULL,
                           project_type VARCHAR
                       (
                           50
                       ) COMMENT '评估类型：资产评估、土地评估、珠宝评估、矿业权评估、咨询',
                           type_code VARCHAR
                       (
                           3
                       ) COMMENT '类型代码：AAP、LAP、JAP、MRV、ACP',
                           manager VARCHAR
                       (
                           100
                       ) NOT NULL,
                           department VARCHAR
                       (
                           100
                       ) COMMENT '业务1组（房地产）、业务2组（固定资产）、业务3组（企业价值）、质控部',
                           estimated_fee DECIMAL
                       (
                           18,
                           2
                       ) COMMENT '预计收费金额',
                           project_date DATE COMMENT '立项日期',
                           base_date DATE COMMENT '评估基准日',
                           client VARCHAR
                       (
                           200
                       ) NOT NULL COMMENT '委托方名称',
                           evaluation_object TEXT NOT NULL COMMENT '评估对象',
                           evaluation_scope TEXT NOT NULL COMMENT '评估范围',
                           purpose TEXT NOT NULL COMMENT '经济行为目的',
                           created_by VARCHAR
                       (
                           50
                       ),
                           created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci
                       """)
        print("[MySQL] 项目表创建完成")

        # 创建索引
        cursor.execute("CREATE INDEX idx_project_no ON projects(project_no)")
        cursor.execute("CREATE INDEX idx_type_code ON projects(type_code)")
        cursor.execute("CREATE INDEX idx_created_date ON projects(created_date)")
        cursor.execute("CREATE INDEX idx_client ON projects(client)")
        print("[MySQL] 索引创建完成")

        # 创建默认管理员用户（部门改为质控部）
        cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                           INSERT INTO users (username, password, realname, department)
                           VALUES ('admin', 'admin123', '系统管理员', '质控部')
                           """)
            print("[MySQL] 默认管理员账号已创建: admin/admin123（部门：质控部）")

        # 创建测试数据（可选）
        create_test_data = input("是否创建测试数据？(y/n): ").lower() == 'y'
        if create_test_data:
            create_test_projects(cursor)

        conn.commit()
        print("[MySQL] 数据库初始化完成！")

    except pymysql.Error as e:
        print(f"[错误] MySQL错误: {e}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


def create_test_projects(cursor):
    """创建测试项目数据"""
    evaluation_types = [
        ('资产评估', 'AAP'),
        ('土地评估', 'LAP'),
        ('珠宝评估', 'JAP'),
        ('矿业权评估', 'MRV'),
        ('咨询', 'ACP')
    ]

    departments = [
        '业务1组（房地产）',
        '业务2组（固定资产）',
        '业务3组（企业价值）',
        '质控部'
    ]

    print("[测试] 正在创建测试数据...")

    for eval_type, type_code in evaluation_types:
        for seq in range(1, 4):
            project_no = f"2026{type_code}{seq:04d}"

            # 插入测试项目
            cursor.execute("""
                           INSERT INTO projects
                           (project_no, project_name, project_type, type_code,
                            manager, department, estimated_fee, project_date, base_date,
                            client, evaluation_object, evaluation_scope, purpose, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           """, (
                               project_no,
                               f"{eval_type}测试项目{seq}",
                               eval_type,
                               type_code,
                               '测试负责人',
                               departments[seq % len(departments)],
                               10000.00 + seq * 1000,
                               '2026-01-06',
                               '2026-01-06',
                               '某某公司',
                               '测试评估对象',
                               '测试评估范围',
                               '测试经济行为目的',
                               'admin'
                           ))

    print("[测试] 测试数据创建完成！")


if __name__ == '__main__':
    print("=" * 60)
    print("MySQL数据库初始化工具（优化版）")
    print("=" * 60)

    print("请在开始前确保:")
    print("  1. MySQL服务正在运行")
    print("  2. 有足够的权限创建数据库和表")
    print("  3. 已在app.py中配置正确的MySQL连接信息")
    print()

    confirm = input("是否继续？(y/n): ").lower()
    if confirm == 'y':
        create_database()
    else:
        print("已取消")

    print("=" * 60)