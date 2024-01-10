import argparse
import torch.nn as nn
from torch.utils.data import DataLoader

from torchvision.models import resnet18 
from torchvision.models import efficientnet
from torchvision.models import mobilenet_v3_small
from torchvision.datasets import ImageFolder
import torchvision.transforms as transforms
import torch
import torch.nn.functional as F

import torchsummary
import copy
# import torchsummary

parser = argparse.ArgumentParser()

parser.add_argument("--n_epochs", type=int, default=100, help="number of epochs of training")

parser.add_argument("--batch_size", type=int, default=128, help="size of the batches")
parser.add_argument("--test_bs", type=int, default=128, help="size of the batches")

parser.add_argument('--device_id', type=str, default='0')
parser.add_argument('--dataset', type=str, default='svhn') # stl10, cifar10, svhn, mnist
parser.add_argument("--n_classes", type=int, default=10, help="number of classes for dataset")
parser.add_argument("--sample_interval", type=int, default=20, help="interval between image sampling")

parser.add_argument('--momentum', type=float, default=0)
parser.add_argument('--weight_decay', type=float, default=0)
parser.add_argument('--data_aug', type=bool, default=False)

args = parser.parse_args()
args.device = 'cuda:' + args.device_id
lr = 1e-1 # MLP

def test_img(net_g, datatest, args):
    net_g.eval()
    net_g.to(args.device)
    # testing
    test_loss = 0
    correct = 0

    data_loader = DataLoader(datatest, batch_size=args.test_bs)
    with torch.no_grad():
      for idx, (data, target) in enumerate(data_loader):
          if 'cuda' in args.device:
              data, target = data.to(args.device), target.to(args.device)
          logits, log_probs = net_g(data)
          test_loss += F.cross_entropy(log_probs, target, reduction='sum').item()
          y_pred = log_probs.data.max(1, keepdim=True)[1]

          correct += y_pred.eq(target.data.view_as(y_pred)).long().cpu().sum()
    test_loss /= len(data_loader.dataset)
    accuracy = 100.00 * correct / len(data_loader.dataset)
    net_g.to('cpu')
    return accuracy, test_loss


# net = resnet18().to(args.device)
# net = resnet18()
net = efficientnet.efficientnet_b0()
print(net.features[0])
# net = mobilenet_v3_small()
# .to('cpu')
# net.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
# # net.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
# net.fc = nn.Linear(512, args.n_classes)
# torchsummary.summary(net, (3,224,224), device='cpu')
torchsummary.summary(net, (3,32,32), device='cpu')

if args.data_aug:
    transform_train = transforms.Compose([  # Previous: Flip, rotation, contrast
        transforms.Grayscale(num_output_channels=1),
        # transforms.RandomHorizontalFlip(),
        # transforms.RandomVerticalFlip(),
        # transforms.RandomCrop(28, padding=4),
        transforms.Resize(32),
        transforms.ToTensor(),
    ])
else:
    transform_train = transforms.Compose([
        transforms.Grayscale(num_output_channels=1),
        transforms.Resize(32),
        transforms.ToTensor(),
    ])
        
# trainset = ImageFolder(root='D:/Spectrum/Train/Train', transform=transform_train)
# testset = ImageFolder(root='D:/Spectrum/Train/Test', transform=transform_train)

loss_func = nn.CrossEntropyLoss()

# ldr_train = DataLoader(trainset, batch_size=args.batch_size, shuffle=True)
optimizer = torch.optim.SGD(net.parameters(), lr=lr, momentum=args.momentum, weight_decay=args.weight_decay)

epoch_loss = []

for iter in range(1,args.n_epochs+1): # train by samples generated by generator
    net.train()
    batch_loss = []

    for batch_idx, (images, labels) in enumerate(ldr_train):
        images, labels = images.to(args.device), labels.to(args.device)
        net.zero_grad()
        logits = net(images)
        loss = F.cross_entropy(logits, labels) ### Check
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        batch_loss.append(loss.item())
    epoch_loss.append(sum(batch_loss)/len(batch_loss))
    print(iter, 'Epoch loss: {:.4f}'.format(epoch_loss[-1]))

    if iter % 5 == 0 or iter == args.n_epochs:
        net.eval()
        acc_test, loss_test = test_img(copy.deepcopy(net), testset, args)
        # if acc_test > best_perf[i]:
        #     best_perf[i] = float(acc_test)            
        print("Testing accuracy " + str(iter) + ": {:.2f}".format(acc_test))
