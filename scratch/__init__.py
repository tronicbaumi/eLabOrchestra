from .blocks import (
    BlockType, BlockCategory, BlockDef, BLOCK_LIBRARY,
    BlockInstance, BlockProgram,
)
from .blockly_bridge   import BlocklyBridge
from .blockly_canvas   import BlocklyCanvas
from .blockly_executor import BlocklyExecutor

# Legacy aliases kept for compatibility
ScratchCanvas = BlocklyCanvas
BlockExecutor = BlocklyExecutor

__all__ = [
    "BlockType", "BlockCategory", "BlockDef", "BLOCK_LIBRARY",
    "BlockInstance", "BlockProgram",
    "BlocklyBridge", "BlocklyCanvas", "BlocklyExecutor",
    "ScratchCanvas", "BlockExecutor",
]
