#!/bin/bash

# --- 基础配置 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 使用环境变量传递密码，彻底消除 Warning 警告
export MYSQL_PWD=$DB_PASS

# 1. 提取数据（直接使用 docker exec 运行并捕获结果）
echo "========================================"
echo "   私有化模型启动方式修改工具"
echo "========================================"
echo "正在提取模型列表，请稍候..."

# 核心修改：使用 -t (分配伪终端) 有时会导致卡住，这里完全不带 -it，直接获取输出
RAW_DATA=$(sudo docker exec $CONTAINER_NAME mysql -u$DB_USER $DB_NAME -N -s -e "SELECT CONCAT(name, '###', IFNULL(worker_name,'NULL')) FROM model_store WHERE source='upload';")

if [ -z "$RAW_DATA" ]; then
    echo "未找到任何 source='upload' 的模型。"
    exit 0
fi

# 2. 解析数据到数组
model_names=()
worker_names=()
i=0

# 使用 while 循环解析
while read -r line; do
    [ -z "$line" ] && continue
    name=$(echo "$line" | awk -F '###' '{print $1}')
    worker=$(echo "$line" | awk -F '###' '{print $2}')
    model_names+=("$name")
    worker_names+=("$worker")
    
    display_worker=$worker
    [ "$worker" == "NULL" ] && display_worker="[未设置]"
    
    echo " [$i] 模型: $name"
    echo "     Worker: $display_worker"
    echo "----------------------------------------"
    ((i++))
done <<< "$RAW_DATA"

# 3. 交互输入修复 (关键：重新定向输入流)
# 在管道执行模式下，必须确保 read 直接访问终端
exec < /dev/tty

echo -n "请选择模型编号: "
read -r model_idx

if [[ ! "$model_idx" =~ ^[0-9]+$ ]] || [ "$model_idx" -ge "${#model_names[@]}" ]; then
    echo "错误: 编号无效。"
    exit 1
fi

target_model=${model_names[$model_idx]}
target_worker=${worker_names[$model_idx]}

echo -e "\n请选择新的 backend_type:"
echo " 1) vllm"
echo " 2) ftransformers"
echo " 3) vox-box"
echo -n "请输入选项 (1-3): "
read -r backend_choice

case $backend_choice in
    1) new_backend="vllm" ;;
    2) new_backend="ftransformers" ;;
    3) new_backend="vox-box" ;;
    *) echo "错误: 选择无效"; exit 1 ;;
esac

# 4. 逻辑判断 worker_name
worker_sql=""
if [ "$target_worker" == "NULL" ] || [ -z "$target_worker" ]; then
    echo -e "\n>>> 自动补全 worker_name: worker qujing 2"
    worker_sql=", worker_name='worker qujing 2'"
else
    echo -e "\n>>> 保持现有 worker_name。"
fi

# 5. 执行更新
echo "正在更新数据库..."
UPDATE_SQL="UPDATE model_store SET backend_type='$new_backend' $worker_sql WHERE name='$target_model' AND source='upload';"
sudo docker exec $CONTAINER_NAME mysql -u$DB_USER $DB_NAME -e "$UPDATE_SQL"

if [ $? -eq 0 ]; then
    echo "========================================"
    echo " 修改成功！"
    echo "========================================"
else
    echo "更新失败，请手动检查数据库。"
fi
