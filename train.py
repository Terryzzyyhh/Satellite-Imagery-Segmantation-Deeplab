'''
用于存放训练代码
'''
import torch
from torch import nn
from torch.optim import Optimizer, Adam
from torch.utils.data import DataLoader
from torch.nn import functional as F
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from tqdm import tqdm
import numpy as np
import argparse

import os
import json

from dataloader import SegDataset
from model import DeepLabV3Res101
from utils import setup, restore_from, save_to, get_metrics
from losses import FocalLoss2d
import config

def train_on_epochs(train_loader:DataLoader, val_loader:DataLoader, ckpt:str=None):
    '''在整个数据集上进行训练
    
    Args:
        train_loader(Dataloader): 训练集加载器
        val_loader(DataLoader): 验证集加载器
        ckpt(str): 从断点恢复
    '''
    print('Setting up model.')
    model = DeepLabV3Res101()
    model, device = setup(model)

    start_ep = 0 # 从指定的epoch开始训练
    if ckpt is not None:
        model, start_ep = restore_from(model, ckpt)

    # 训练时的各种指标
    info = {'train': [], 'val': []}

    save_path = './checkpoints'
    if not os.path.exists(save_path):
        os.mkdir(save_path)

    # 设定优化器
    optimizer = Adam(model.parameters(), lr=config.train_config['lr'])

    # 设定Loss
    criterion = FocalLoss2d().to(device)

    # 开始执行训练
    for ep in range(start_ep, config.train_config['max_epoch']):
        train_info = train(model, train_loader, optimizer, ep, device, criterion)
        val_info = validate(model, val_loader, optimizer, ep, device, criterion)
        # 保存信息
        info['train'] += train_info
        info['val'] += val_info
        # 保存模型
        save_to(model, save_path, ep)

    with open('./info.json', 'w') as f:
        json.dump(info, f)
    
    print('Done.')

def train(model:nn.Module, dataloader:DataLoader, optimizer:Optimizer, ep:int,
        device:int, criterion:nn.Module):
    '''训练模型

    Args:
        model(nn.Module): 待训练模型
        dataloader(DataLoader): 数据加载器
        optimizer(Optimizer): 优化器
        device(torch.Device): 运行环境

    Return:
        train_info(list): 训练时的log
    '''
    model.train()

    train_info = []

    print('Size of training set: {}'.format(len(dataloader.dataset)))

    # 执行训练
    for step, (X, y, _) in enumerate(dataloader):
        X = X.to(device) # type: torch.Tensor
        y = y.to(device) # type: torch.Tensor
        optimizer.zero_grad()
        y_ = model(X) # type: torch.Tensor
        loss = criterion(y_, y) # type: torch.Tensor
        loss.backward()
        optimizer.step()

        y_ = y_.argmax(dim=1).cpu().numpy()
        y = y.cpu().numpy()
        
        # 计算运行时指标
        miou, _, mpa = get_metrics(y, y_)
        # 保存训练时数据
        train_info.append([ep, loss.item(), mpa, miou])

        # 输出信息
        if (step + 1) % config.train_config['log_interval'] == 0:
            print('[Epoch %2d - %3d of %3d]mpa: %.2f, miou: %.2f, loss: %.2f'\
                % (ep, step + 1, len(dataloader), mpa, miou, loss.item()))
    return train_info

def validate(model:nn.Module, test_dataloader:DataLoader, optimizer:Optimizer, ep:int,
        device:int, criterion:nn.Module):
    '''验证模型

    Args:
        model(nn.Module): 待测试模型
        dataloader(DataLoader): 数据加载器
        optimizer(Optimizer): 优化器
        device(torch.Device): 运行环境

    Return:
        test_info(list): 测试时的log
    '''
    print('Size of test set: ', len(test_dataloader))

    mious, mpas = [], []
    test_info = []
    total_loss = 0
    model.eval()

    with torch.no_grad():
        for X, y, _ in tqdm(test_dataloader, desc='Validating'):
            X, y = X.to(device), y.to(device)
            y_ = model(X)
            loss = criterion(y_, y)
            total_loss += loss.item()
            y_ = y_.argmax(dim=1)
            y_gd = y.cpu().numpy()
            y_pred = y_.cpu().numpy()
            miou, _, mpa = get_metrics(y_gd, y_pred)
            mious.append(miou)
            mpas.append(mpa)

    avg_loss = total_loss / len(test_dataloader) / test_dataloader.batch_size
    miou = np.average(mious)
    mpa = np.average(mpas)
    test_info.append([ep, avg_loss, mpa, miou])

    print('[Epoch %2d]Test avg loss: %.4f, mpa: %.2f, mIoU: %.2f' % (ep, avg_loss, mpa, miou))

    return test_info

def setup_dataloader(train_path:str):
    '''构建训练用数据集，包含训练集和测试集

    Args:
        train_path(str): 训练数据集路径
    
    Return:
        train_loader(Dataloader): 训练集加载器
        val_loader(Dataloader): 验证集加载器
    '''
    list_full_path = lambda path: [os.path.join(path, f) for f in os.listdir(path)]
    split_size = 0.2 # 选取0.2作为验证集
    X_list = list_full_path(os.path.join(train_path, 'img'))
    y_list = list_full_path(os.path.join(train_path, 'label'))
    X_train, X_val, y_train, y_val = train_test_split(X_list, y_list, test_size=split_size)
    dataset = {}
    for name, data_list, label_list in [('train', X_train, y_train), ('val', X_val, y_val)]:
        dataset[name] = DataLoader(SegDataset(data_list, label_list, name, **config.dataset_config), **config.dataloader_config)
    return dataset['train'], dataset['val']

def parse_args():
    parser = argparse.ArgumentParser(usage='python3 train.py -t path/to/train/data -v path/to/val/data -r path/to/checkpoint')
    parser.add_argument('-t', '--train_path', help='path to your datasets', default='./data/train')
    parser.add_argument('-r', '--restore_from', help='path to the checkpoint', default=None)
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    names = ['train', 'val']
    args = parse_args()
    assert os.path.exists(args.train_path), '请指定训练数据集路径'
    print('Setting up dataloaders.')
    train_loader, val_loader = setup_dataloader(args.train_path)
    train_on_epochs(train_loader, val_loader, args.restore_from)