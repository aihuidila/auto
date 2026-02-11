#!/bin/bash

# --- 基础配置 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 数据库执行函数 - 去掉 -i 选项，防止抢占管道输入
function db_query() {
    # 仅使用 docker exec (不带 -i) 执行命令
    sudo docker exec $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -N -s -e \"$1\""
}

function db_update() {
    sudo docker exec $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -e \"$1\""
}

echo "========================================"
echo "   私有化模型启动方式修改工具"
echo "========================================"

# 1. 查询模型
echo "正在提取 source='upload' 的模型列表..."
# 获取数据
RAW_DATA=$(db_query "SELECT CONCAT(name, ':', IFNULL(worker_name,'NULL')) FROM model_store WHERE source='upload';")

if [ -z "$RAW_DATA" ]; then
    echo "未找到任何私有化导入的模型。"
    exit 0
fi

# 2. 解析数据并显示
model_names=()
worker_names=()
i=0

# 使用 while 循环配合变量，不从 stdin 读取
while IFS=':' read -r name worker; do
    model_names+=("$name")
    worker_names+=("$worker")
    echo " [$i] 模型名称: $name"
    echo "     当前 Worker: $worker"
    echo "----------------------------------------"
    ((i++))
done <<< "$RAW_DATA"

# 3. 交互输入 - 必须强制指向终端 /dev/tty
echo -n "请选择模型编号: "
read -r model_idx < /dev/tty

if [[ ! "$model_idx" =~ ^[0-9]+$ ]] || [ "$model_idx" -ge "${#model_names[@]}" ]; then
    echo "错误: 编号无效。"
    exit 1
fi

target_model=${model_names[$model_idx]}
target_worker=${worker_names[$model_idx]}

# 4. 选择 Backend
echo -e "\n请选择新的 backend_type:"
echo " 1) vllm"
echo " 2) ftransformers"
echo " 3) vox-box"
echo -n "请输入选项 (1-3): "
read -r backend_choice < /dev/tty

case $backend_choice in
    1) new_backend="vllm" ;;
    2) new_backend="ftransformers" ;;
    3) new_backend="vox-box" ;;
    *) echo "错误: 选择无效"; exit 1 ;;
esac

# 5. 处理 worker_name
worker_sql=""
if [ "$target_worker" == "NULL" ] || [ -z "$target_worker" ] || [ "$target_worker" == "" ]; then
    echo -e "\n>>> 检测到 worker_name 为空，补全为: worker qujing 2"
    worker_sql=", worker_name='worker qujing 2'"
else
    echo -e "\n>>> 保持原有 worker_name: $target_worker"
fi

# 6. 执行更新
echo "正在提交更新到数据库..."
final_sql="UPDATE model_store SET backend_type='$new_backend' $worker_sql WHERE name='$target_model' AND source='upload';"
db_update "$final_sql"

if [ $? -eq 0 ]; then
    echo "========================================"
    echo " 修改成功！"
    echo "========================================"
else
    echo "错误: 更新失败。"
fi
