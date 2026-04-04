# 代码优化快速参考

## 修复汇总表

| 问题 | 原文件 | 修复方式 | 重要性 |
|------|--------|---------|--------|
| #6 | app.py | 移除 rumps 私有 API 使用 | 🔴 高 |
| #7 | app.py, cli.py | 添加异常堆栈跟踪 | 🔴 高 |
| #8 | cli.py | CLI end 命令传入 config | 🟡 中 |
| #9 | pipeline.py | 避免修改原始 dict | 🟡 中 |
| #10 | timeline.py | 传入 workspace_dir 相对化路径 | 🟡 中 |
| #11 | config.py | 增强 JSON 异常处理 | 🟡 中 |
| #12 | workspace.py | assert 改为异常检查 | 🟡 中 |
| #13 | hotkeys.py | 热键验证并抛出异常 | 🟡 中 |
| #15 | app.py, workspace.py | _save_meta() → save_meta() | 🟡 中 |
| #16 | templates.py | 删除未使用 import copy | 🟢 低 |
| #17 | app.py | 删除重复 import Path | 🟢 低 |
| #18 | workspace.py | import re 移到顶部 | 🟢 低 |
| #19 | hotkeys.py | 删除不可达代码 | 🟢 低 |
| #20 | timeline.py, templates.py | 提取共享 short_time() | 🟢 低 |
| #21 | capture.py, app.py | 使用 CLIPBOARD_IMAGE_MARKER | 🟢 低 |
| #22 | hotkeys.py | 修正 CFMachPort 类型 | 🟢 低 |
| #25 | data/ | 删除无用目录 | 🟢 低 |
| #26 | README.md | 更新过时的 pynput 说明 | 🟢 低 |

## 文件修改一览

### 新增文件
- `FIXES_SUMMARY.md` - 详细修复总结
- `OPTIMIZATION_REPORT.md` - 完整优化报告

### 修改文件
- ✏️ `app.py` - 异常追踪、API 改进、常量使用
- ✏️ `cli.py` - config 参数传递
- ✏️ `pipeline.py` - dict 安全性改进
- ✏️ `timeline.py` - 时间函数提取、参数改进
- ✏️ `config.py` - 新增常量、函数、异常处理
- ✏️ `capture.py` - 常量使用
- ✏️ `templates.py` - 导入清理、函数重用
- ✏️ `workspace.py` - API 改进、异常处理、导入整理
- ✏️ `hotkeys.py` - 类型修正、验证、死代码清理
- ✏️ `README.md` - 文档更新

### 删除文件
- 🗑️ `data/.gitkeep`

## 新增 API

### 常量 (config.py)
```python
CLIPBOARD_IMAGE_MARKER = "__clipboard_image__"
SHORT_TIME_PATTERN = short_time
```

### 函数
```python
# config.py
def short_time(timestamp: str) -> str

# workspace.py
def save_meta(self) -> None  # 原 _save_meta() 改为公开
```

### 改进的函数签名
```python
# config.py
def read_json(path: Path) -> dict  # 现在抛出 JSONDecodeError

# hotkeys.py
def __init__(...) -> None  # 现在验证热键并抛出异常
```

## 代码风格改进

### 异常处理
```python
# Before
except Exception:
    pass

# After
except Exception as e:
    import traceback
    traceback.print_exc()
```

### 私有 API 避免
```python
# Before
win._alert.window()
win._textfield

# After
# 已通过公开的 _get_focus_target_class() 和 _run_window() 隐藏
```

### 常量化
```python
# Before
return "__clipboard_image__", "image"

# After
return CLIPBOARD_IMAGE_MARKER, "image"
```

### 数据安全
```python
# Before
entry["pipeline"] = name
all_entries.append(entry)  # 修改了原始数据

# After
entry_with_pipeline = {**entry, "pipeline": name}
all_entries.append(entry_with_pipeline)  # 原始数据不变
```

## 验证清单

- [x] 所有 Python 文件编译通过
- [x] 无新的 linter 错误
- [x] 向后兼容性保证
- [x] 私有 API 依赖移除
- [x] 异常处理增强
- [x] 常量化魔术字符串
- [x] 导入整理
- [x] 死代码清理
- [x] 文档更新

## 使用指南

### 使用新常量
```python
from .config import CLIPBOARD_IMAGE_MARKER

if content == CLIPBOARD_IMAGE_MARKER:
    # 处理剪贴板图片
```

### 使用时间函数
```python
from .config import short_time

time_short = short_time(timestamp)  # 替代手动 split
```

### 处理异常
```python
from .hotkeys import HotkeyManager

try:
    mgr = HotkeyManager(...)
except ValueError as e:
    print(f"热键配置错误: {e}")
```

---
快速参考完毕。详见各文件注释和 FIXES_SUMMARY.md。
