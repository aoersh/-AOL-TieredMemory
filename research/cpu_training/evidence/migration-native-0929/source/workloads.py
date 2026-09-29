"""Fixed eager FP32 correctness workloads; attention explicitly uses matmul/softmax."""
import math
import torch
from torch import nn


class EncoderBlock(nn.Module):
    def __init__(self, width, heads):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.LayerNorm(width)
        self.qkv = nn.Linear(width, 3 * width)
        self.projection = nn.Linear(width, width)
        self.norm2 = nn.LayerNorm(width)
        self.ff = nn.Sequential(nn.Linear(width, 4 * width), nn.GELU(),
                                nn.Linear(4 * width, width))

    def forward(self, x):
        batch, length, width = x.shape
        qkv = self.qkv(self.norm1(x)).reshape(batch, length, 3, self.heads,
                                             width // self.heads)
        q, k, v = qkv.permute(2, 0, 3, 1, 4).unbind(0)
        weights = torch.softmax((q @ k.transpose(-2, -1)) / math.sqrt(width // self.heads), dim=-1)
        context = (weights @ v).transpose(1, 2).contiguous().reshape(batch, length, width)
        x = x + self.projection(context)
        return x + self.ff(self.norm2(x))


def build(args):
    if args.workload == 'mlp':
        model = nn.Sequential(nn.Linear(512, 1024), nn.GELU(),
                              nn.LayerNorm(1024), nn.Linear(1024, 256))
        return model, torch.randn(args.batch, 512), torch.randn(args.batch, 256)
    model = nn.Sequential(*(EncoderBlock(args.width, args.heads) for _ in range(args.layers)),
                          nn.LayerNorm(args.width), nn.Linear(args.width, args.width))
    shape = (args.batch, args.sequence, args.width)
    return model, torch.randn(shape), torch.randn(shape)
