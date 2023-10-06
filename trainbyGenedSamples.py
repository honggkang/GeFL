'''
Train the classifier network by
samples generated by generator

- trainbyGen.py
train a classifier by online samples generated by generator.

- trainbyGenedSamples.py
train a classifier by offline samples generated by generator.
DDPM takes so much time to generate samples.
DDPM generates beforehand by imageGeneration.py
'''

import argparse
import torch.nn as nn

from modelsMNIST.GAN import *
from modelsMNIST.VAE import *
from modelsMNIST.DDPM import *

from FeatureExtractor.mlp import *
from utils.util import test_img
from utils.getData import *
import copy

from torchvision.datasets import ImageFolder
from torch.utils.data import DataLoader

parser = argparse.ArgumentParser()
parser.add_argument("--gen_n_epochs", type=int, default=70, help="number of epochs of training")
parser.add_argument("--n_epochs", type=int, default=0, help="number of epochs of training")
parser.add_argument("--batch_size", type=int, default=32, help="size of the batches")
parser.add_argument("--bs", type=int, default=128, help="size of the batches")

parser.add_argument('--device_id', type=str, default='2')
parser.add_argument('--dataset', type=str, default='mnist') # stl10, cifar10, svhn, mnist
parser.add_argument('--gen', type=str, default='ddpm') # gan, vae, ddpm
parser.add_argument('--models', type=str, default='mlp') # cnn, mlp

# parser.add_argument("--lr", type=float, default=0.0002, help="adam: learning rate")
# parser.add_argument("--b1", type=float, default=0.5, help="adam: decay of first order momentum of gradient")
# parser.add_argument("--b2", type=float, default=0.999, help="adam: decay of first order momentum of gradient")
parser.add_argument("--n_cpu", type=int, default=8, help="number of cpu threads to use during batch generation")

parser.add_argument("--n_feat", type=int, default=128) # DDPM
parser.add_argument("--w", type=str, default='2') # DDPM guidance

parser.add_argument("--n_classes", type=int, default=10, help="number of classes for dataset")
parser.add_argument("--img_size", type=int, default=28, help="size of each image dimension")
parser.add_argument("--channels", type=int, default=1, help="number of image channels")
parser.add_argument("--sample_interval", type=int, default=5, help="interval between image sampling")

parser.add_argument('--momentum', type=float, default=0)
parser.add_argument('--weight_decay', type=float, default=0)

parser.add_argument('--partial_data', type=float, default=0.01)
parser.add_argument('--rs', type=int, default=2, help='random seed')

args = parser.parse_args()
args.device = 'cuda:' + args.device_id

torch.manual_seed(args.rs)
torch.cuda.manual_seed(args.rs)
torch.cuda.manual_seed_all(args.rs) # if use multi-GPU
np.random.seed(args.rs)
random.seed(args.rs)

lr = 1e-1 # MLP

net = MLP3().to(args.device)
loss_func = nn.CrossEntropyLoss()

dataset_train, dataset_test = getDataset(args)

dict_users = cifar_iid(dataset_train, int(1/args.partial_data), args.rs)

tf = transforms.Compose([transforms.ToTensor(), transforms.Grayscale()]) # mnist is already normalised 0 to 1 / imageFolder loads data by 3 channels
train_imagefolder = ImageFolder(root='./generatedImages/mnist/10ddpm' + args.w + '_nTt100nTg100',
                                transform=tf)
dataloader = DataLoader(train_imagefolder, batch_size=args.batch_size, shuffle=True)
optimizer = torch.optim.SGD(net.parameters(), lr=lr, momentum=args.momentum, weight_decay=args.weight_decay)

gen_epoch_loss = []
best_perf =0
# print('gen sample iteration',self.iter)
for iter in range(1, args.gen_n_epochs+1): # train by samples generated by generator
    net.train()
    gen_batch_loss = []
    for i, (imgs, labels) in enumerate(dataloader):
        imgs, labels = imgs.to(args.device), labels.to(args.device)
        net.zero_grad()
        logits, log_probs = net(imgs)
        loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        gen_batch_loss.append(loss.item())
    gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))
    print(iter, 'Epoch loss: {:.4f}'.format(gen_epoch_loss[-1]))

    if iter % 10 == 0 or iter == args.n_epochs:
        net.eval()
        acc_test, loss_test = test_img(copy.deepcopy(net), dataset_test, args)
        if acc_test > best_perf:
            best_perf = float(acc_test)
        print("Testing accuracy " + str(iter) + ": {:.2f}".format(acc_test))
        print("Best accuracy {:.2f}".format(best_perf))
        # if args.wandb:
        #     wandb.log({
        #         "Communication round": iter,
        #         "Local model " + str(i) + " test accuracy": acc_test
        #     })
# gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

ldr_train = DataLoader(DatasetSplit(dataset_train, dict_users[0]), batch_size=args.batch_size, shuffle=True)
optimizer = torch.optim.SGD(net.parameters(), lr=lr, momentum=args.momentum, weight_decay=args.weight_decay)
epoch_loss = []

for iter in range(1, args.n_epochs+1):
    net.train()
    batch_loss = []
    
    for batch_idx, (images, labels) in enumerate(ldr_train):
        images, labels = images.to(args.device), labels.to(args.device)
        net.zero_grad()
        logits, log_probs = net(images)
        loss = F.cross_entropy(logits, labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        batch_loss.append(loss.item())
    epoch_loss.append(sum(batch_loss)/len(batch_loss))
    print(iter, 'Epoch loss: {:.4f}'.format(epoch_loss[-1]))

    if iter % 10 == 0 or iter == args.n_epochs:
        net.eval()
        acc_test, loss_test = test_img(copy.deepcopy(net), dataset_test, args)
        print("Testing accuracy " + str(iter) + ": {:.2f}".format(acc_test))
        # if args.wandb:
        #     wandb.log({
        #         "Communication round": iter,
        #         "Local model " + str(i) + " test accuracy": acc_test
        #     })
