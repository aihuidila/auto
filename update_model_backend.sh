#!/bin/bash

# --- 基础配置 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 数据库执行函数
function db_query() {
    # 增加错误输出重定向，方便定位是否是 docker 或 mysql 报错
    sudo docker exec -i $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -N -s -e \"$1\"" 2>&1
}

function db_update() {
    sudo docker exec -i $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -e \"$1\""
}

echo "========================================"
echo "   私有化模型启动方式修改工具"
echo "========================================"

# 1. 检查容器状态
if ! sudo docker ps | grep -q "$CONTAINER_NAME"; then
    echo "错误: 容器 $CONTAINER_NAME 未运行。"
    exit 1
fi

# 2. 查询模型
echo "正在提取 source='upload' 的模型列表..."
# 获取数据，使用 | 分隔
RAW_DATA=$(db_query "SELECT CONCAT(name, '|', IFNULL(worker_name,'NULL')) FROM model_store WHERE source='upload';")

# 调试：如果数据为空，打印原因
if [ -z "$RAW_DATA" ]; then
    echo "提示: 查询结果为空。可能原因："
    echo "  1. 数据库中没有 source='upload' 的记录"
    echo "  2. 数据库连接失败（请检查密码）"
    exit 0
fi

# 3. 解析数据
model_names=()
worker_names=()

# 强制将结果按行存入数组
IFS=$'\n' read -rd '' -a lines <<< "$RAW_DATA"

i=0
for line in "${lines[@]}"; do
    [[ -z "$line" ]] && continue
    name=$(echo "$line" | cut -d'|' -f1)
    worker=$(echo "$line" | cut -d'|' -f2)
    model_names+=("$name")
    worker_names+=("$worker")
    echo " [$i] 模型名称: $name"
    echo "     当前 Worker: ${worker}"
    echo "----------------------------------------"
    ((i++))
done

# 如果数组为空，说明解析失败
if [ ${#model_names[@]} -eq 0 ]; then
    echo "解析模型数据失败，请检查输出: $RAW_DATA"
    exit 1
fi

# 4. 交互输入 (关键：强制从当前终端获取输入)
echo -n "请选择模型编号: "
read -r model_idx < /dev/tty

if [[ ! "$model_idx" =~ ^[0-9]+$ ]] || [ "$model_idx" -ge "${#model_names[@]}" ]; then
    echo "错误: 输入编号 [$model_idx] 无效。"
    exit 1
fi

target_model=${model_names[$model_idx]}
target_worker=${worker_names[$model_idx]}

# 5. 选择 Backend
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

# 6. 处理 worker_name
worker_sql=""
if [ "$target_worker" == "NULL" ] || [ -z "$target_worker" ]; then
    echo -e "\n>>> 补全 worker_name 为: worker qujing 2"
    worker_sql=", worker_name='worker qujing 2'"
else
    echo -e "\n>>> 保持原有 worker_name: $target_worker"
fi

# 7. 执行更新
echo "正在提交更新..."
final_sql="UPDATE model_store SET backend_type='$new_backend' $worker_sql WHERE name='$target_model' AND source='upload';"
db_update "$final_sql"

if [ $? -eq 0 ]; then
    echo "========================================"
    echo " 修改成功！"
    echo "========================================"
else
    echo "错误: 更新失败。"
fi
