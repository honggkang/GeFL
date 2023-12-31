'''
- 1. Main networks (including a common feature extrator (FE)) are first trained by warming up stages
- 2. Generator is trained to generate feature (outpu by a common FE) by warming up stages

Clients train main networks with local dataset
Clients share common feature extractor and a server FedAvg feature extrator
LOOP:
    === Training Generator ===
    1. Clients create intermittent features of local dataset
    2. Clients train generator by local feature dataset and share it - FedAvg

    === Training Main Net ===
    3. Clients train main networks with local dataset and features sampled by generator
'''
'''
Each client: get parameter from ws_glob and local update. Save updated parameter to ws_local
FedAvg ws_local and save it ws_glob
'''
from torchvision.utils import save_image

import argparse
import os
import wandb
from datetime import datetime
import numpy as np
import random
import torch
import copy

from utils.localUpdateRaw import *
from utils.average import *
from utils.getData import *
from utils.getModels import *

from mlp_generators.GAN import *
from utils.util import test_img, get_logger

parser = argparse.ArgumentParser()

### clients
parser.add_argument('--num_users', type=int, default=1)
parser.add_argument('--frac', type=float, default=1)
parser.add_argument('--partial_data', type=float, default=0.1)
### model & feature size
parser.add_argument('--models', type=str, default='mlp') # cnn, mlp
parser.add_argument('--output_channel', type=int, default=1, help='channel size of image generator generates') # local epochs for training main nets by generated samples
parser.add_argument('--img_size', type=int, default=14) # local epochs for training generator
### dataset
parser.add_argument('--dataset', type=str, default='mnist') # stl10, cifar10, svhn, mnist, fmnist
parser.add_argument('--noniid', action='store_true') # default: false
parser.add_argument('--dir_param', type=float, default=0.3)
parser.add_argument('--num_classes', type=int, default=10)
### optimizer
parser.add_argument('--bs', type=int, default=64)
parser.add_argument('--local_bs', type=int, default=64)
parser.add_argument('--momentum', type=float, default=0)
parser.add_argument('--weight_decay', type=float, default=0)
### reproducibility
parser.add_argument('--rs', type=int, default=3)
parser.add_argument('--num_experiment', type=int, default=1, help="the number of experiments")
parser.add_argument('--device_id', type=str, default='1')
### warming-up
parser.add_argument('--gen_wu_epochs', type=int, default=400) # warm-up epochs for generator

parser.add_argument('--epochs', type=int, default=0) # total communication round (train main nets by (local samples and gen) + train gen)
parser.add_argument('--local_ep', type=int, default=5) # local epochs for training main nets by local samples
parser.add_argument('--local_ep_gen', type=int, default=1) # local epochs for training main nets by generated samples
parser.add_argument('--gen_local_ep', type=int, default=1) # local epochs for training generator

parser.add_argument('--aid_by_gen', type=bool, default=True)
parser.add_argument('--freeze_gen', type=bool, default=False)
parser.add_argument('--only_gen', type=bool, default=False)
parser.add_argument('--avg_FE', type=bool, default=True)
### logging
parser.add_argument('--sample_test', type=int, default=20) # local epochs for training generator
parser.add_argument('--save_imgs', type=bool, default=True) # local epochs for training generator
parser.add_argument('--wandb', type=bool, default=False)
parser.add_argument('--name', type=str, default='1u_14') # L-A: bad character
### GAN parameters
parser.add_argument("--b1", type=float, default=0.5, help="adam: decay of first order momentum of gradient")
parser.add_argument("--b2", type=float, default=0.999, help="adam: decay of first order momentum of gradient")
parser.add_argument('--lr', type=float, default=0.0002) # GAN lr
parser.add_argument('--latent_dim', type=int, default=100)
### N/A
parser.add_argument('--wu_epochs', type=int, default=0) # warm-up epochs for main networks
parser.add_argument('--freeze_FE', type=bool, default=False) # N/A

args = parser.parse_args()
args.device = 'cuda:' + args.device_id
args.img_shape = (args.output_channel, args.img_size, args.img_size)
args.feature_size = args.img_size

dataset_train, dataset_test = getDataset(args)
# img_size = dataset_train[0][0].shape
print(args)


# def initialize_weights(model):
#     classname = model.__class__.__name__
#     # fc layer
#     if classname.find('Linear') != -1:
#         nn.init.normal_(model.weight.data, 0.0, 0.02)
#         nn.init.constant_(model.bias.data, 0)
#     # batchnorm
#     elif classname.find('BatchNorm') != -1:
#         nn.init.normal_(model.weight.data, 0.0, 0.02)
#         nn.init.constant_(model.bias.data, 0)


def main():

    tf = transforms.Compose([transforms.Resize(args.img_size),transforms.ToTensor(),transforms.Normalize([0.5], [0.5])]) # mnist is already normalised 0 to 1
    if args.dataset == 'mnist':
        train_data = datasets.MNIST(root='/home/hong/NeFL/.data/mnist', train=True, transform=tf, download=True)
    elif args.dataset == 'fmnist':
        train_data = datasets.FashionMNIST(root='/home/hong/NeFL/.data/fmnist', train=True, transform=tf, download=True)

    if args.noniid:
        dict_users = noniid_dir(args, args.dir_param, dataset_train)
    else:
        dict_users = cifar_iid(train_data, int(1/args.partial_data*args.num_users), args.rs)

    if not args.aid_by_gen:
        args.gen_wu_epochs = 0
        args.local_ep_gen = 0
        args.gen_local_ep = 0
        
    local_models, common_net = getModel(args)
    w_comm = common_net.state_dict()
    ws_glob = []
    for _ in range(args.num_models):
        ws_glob.append(local_models[_].state_dict())

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = './output/gefl/'+ timestamp + str(args.name) + str(args.rs)
    if not os.path.exists(filename):
        os.makedirs(filename)
    if args.wandb:
        run = wandb.init(dir=filename, project='GeFL-GAN-onlySyn-1024', name= str(args.name)+ str(args.rs), reinit=True, settings=wandb.Settings(code_dir="."))
        wandb.config.update(args)
    # logger = get_logger(logpath=os.path.join(filename, 'logs'), filepath=os.path.abspath(__file__))
    
    loss_train = []
    lr = 1e-1 # MLP

    gen_glob = Generator(args).to(args.device)
    dis_glob = Discriminator(args).to(args.device)
    # gen_glob.apply(initialize_weights)
    # dis_glob.apply(initialize_weights)
    
    # optgs , optds = [], []    
    optg = torch.optim.Adam(gen_glob.parameters(), lr=args.lr, betas=(args.b1, args.b2)).state_dict()
    optd = torch.optim.Adam(dis_glob.parameters(), lr=args.lr, betas=(args.b1, args.b2)).state_dict()
 
    optgs = [copy.deepcopy(optg) for _ in range(args.num_users)]
    optds = [copy.deepcopy(optd) for _ in range(args.num_users)]

    # gen_w_glob = gen_glob.state_dict()
    # dis_w_glob = dis_glob.state_dict()
    
    for iter in range(1, args.gen_wu_epochs+1):
        ''' ---------------------------
        Warming up for generative model
        --------------------------- '''
        gen_w_local = []
        dis_w_local = []
        gloss_locals = []
        dloss_locals = []
        
        m = max(int(args.frac * args.num_users), 1)
        idxs_users = np.random.choice(range(args.num_users), m, replace=False)
        
        for idx in idxs_users:

            local = LocalUpdate_GAN_raw(args, dataset=train_data, idxs=dict_users[idx])
            g_weight, d_weight, gloss, dloss, optgs[idx], optds[idx] = local.train(gnet=copy.deepcopy(gen_glob), dnet=copy.deepcopy(dis_glob), optg=optgs[idx], optd=optds[idx])

            gen_w_local.append(copy.deepcopy(g_weight))
            dis_w_local.append(copy.deepcopy(d_weight))
            
            gloss_locals.append(gloss)
            dloss_locals.append(dloss)
        
        gen_w_glob = FedAvg(gen_w_local)
        dis_w_glob = FedAvg(dis_w_local)
        
        gloss_avg = sum(gloss_locals) / len(gloss_locals)
        dloss_avg = sum(dloss_locals) / len(dloss_locals)

        gen_glob.load_state_dict(gen_w_glob)
        dis_glob.load_state_dict(dis_w_glob)

        if args.save_imgs and (iter % args.sample_test == 0 or iter == args.gen_wu_epochs):
            gen_glob.eval()
            sample_num = 100
            samples = gen_glob.sample_image_4visualization(sample_num)
            save_image(samples.view(sample_num, args.output_channel, args.img_size, args.img_size),
                        'imgs/imgFedGAN/' + str(args.name)+ str(args.rs) + '_' + str(iter) + '.png', nrow=10)
            gen_glob.train()

            # gen_glob.eval()
            # z = Variable(FloatTensor(np.random.normal(0, 1, (100, args.latent_dim))))
            # # Get labels ranging from 0 to n_classes for n rows
            # labels = np.array([num for _ in range(10) for num in range(10)])
            # labels = Variable(LongTensor(labels))
            # gen_imgs = gen_glob(z, labels)
            # gen_imgs = gen_imgs.view(100, args.output_channel, args.img_size, args.img_size)
            # save_image(gen_imgs.data, "imgs/imgFedGAN/np_foward" + str(args.name) + str(args.rs) + "_%d.png" %iter, nrow=10, normalize=True)
            # gen_glob.train()

        print('Warm-up GEN Round {:3d}, G Avg loss {:.3f}, D Avg loss {:.3f}'.format(iter, gloss_avg, dloss_avg))
        

    best_perf = [0 for _ in range(args.num_models)]

    for iter in range(1, args.epochs+1):
        ''' ----------------------------------------
        Train main networks by local sample
        and generated samples, then update generator
        ---------------------------------------- '''
        ws_local = [[] for _ in range(args.num_models)]
        gen_w_local = []
        dis_w_local = []
        
        loss_locals = []
        gen_loss_locals = []
        
        gloss_locals = []
        dloss_locals = []
        
        m = max(int(args.frac * args.num_users), 1)
        idxs_users = np.random.choice(range(args.num_users), m, replace=False)
        if args.aid_by_gen:
            gen_glob.load_state_dict(gen_w_glob)
            dis_glob.load_state_dict(dis_w_glob)
        
        for idx in idxs_users:
            dev_spec_idx = min(idx//(args.num_users//args.num_models), args.num_models-1)
            model_idx = dev_spec_idx
            model = local_models[model_idx]
            model.load_state_dict(ws_glob[model_idx])
            
            if args.only_gen:
                local = LocalUpdate_onlyGen(args, dataset=dataset_train, idxs=dict_users[idx])
                weight, loss, gen_loss = local.train(net=copy.deepcopy(model).to(args.device), gennet=copy.deepcopy(gen_glob), learning_rate=lr)
            else:
                local = LocalUpdate(args, dataset=dataset_train, idxs=dict_users[idx])
                # synthetic data updates header & real data updates whole target network
                if args.aid_by_gen:
                    weight, loss, gen_loss = local.train(net=copy.deepcopy(model).to(args.device), gennet=copy.deepcopy(gen_glob), learning_rate=lr)
                else:
                    weight, loss, gen_loss = local.train(net=copy.deepcopy(model).to(args.device), learning_rate=lr)                

            ws_local[model_idx].append(weight)
            loss_locals.append(loss)
            gen_loss_locals.append(gen_loss)
            
            if args.aid_by_gen and not args.freeze_gen: # update GEN
                local_gen = LocalUpdate_GAN_raw(args, dataset=train_data, idxs=dict_users[idx])
                # g_weight, d_weight, gloss, dloss = local_gen.train(gnet=copy.deepcopy(gen_glob), dnet=copy.deepcopy(dis_glob))
                g_weight, d_weight, gloss, dloss, optgs[idx], optds[idx] = local_gen.train(gnet=copy.deepcopy(gen_glob), dnet=copy.deepcopy(dis_glob), optg=optgs[idx], optd=optds[idx])

                gen_w_local.append(copy.deepcopy(g_weight))
                dis_w_local.append(copy.deepcopy(d_weight))

                gloss_locals.append(gloss)
                dloss_locals.append(dloss)
                
        if args.aid_by_gen and not args.freeze_gen:
            gloss_avg = sum(gloss_locals) / len(gloss_locals)
            dloss_avg = sum(dloss_locals) / len(dloss_locals)
            
            gen_w_glob = FedAvg(gen_w_local)
            dis_w_glob = FedAvg(dis_w_local)
            if args.save_imgs and (iter % args.sample_test == 0 or iter == args.epochs):
                sample_num = 40
                samples = gen_glob.sample_image_4visualization(sample_num)
                save_image(samples.view(sample_num, args.output_channel, args.img_size, args.img_size),
                            'imgs/imgFedGAN/' + str(args.name)+ str(args.rs) + 'SynOrig_' + str(args.gen_wu_epochs+iter) + '.png', nrow=10, normalize=True)
            print('GEN Round {:3d}, G Avg loss {:.3f}, D Avg loss {:.3f}'.format(args.gen_wu_epochs+iter, gloss_avg, dloss_avg))
        else:
            gloss_avg = -1
            dloss_avg = -1
        '''
        FedAvg_FE: LG-FedAVG
        FedAvg_FE_raw: FedAVG
        '''
        if args.avg_FE:
            ws_glob, w_comm = FedAvg_FE(args, ws_glob, ws_local, w_comm) # main net, feature extractor weight update
        else:
            ws_glob = FedAvg_FE_raw(args, ws_glob, ws_local)

        loss_avg = sum(loss_locals) / len(loss_locals)
        try:
            gen_loss_avg = sum(gen_loss_locals) / len(gen_loss_locals)
        except:
            gen_loss_avg = -1
        print('Round {:3d}, Avg loss {:.3f}, Avg loss by Gen samples {:.3f}, G Avg loss {:.3f}, D Avg loss {:.3f}'.format(iter, loss_avg, gen_loss_avg, gloss_avg, dloss_avg))

        loss_train.append(loss_avg)
        if iter % args.sample_test == 0 or iter == args.epochs:
            acc_test_tot = []

            for i in range(args.num_models):
                model_e = local_models[i]
                model_e.load_state_dict(ws_glob[i])
                model_e.eval()
                acc_test, loss_test = test_img(model_e, dataset_test, args)
                if acc_test > best_perf[i]:
                    best_perf[i] = float(acc_test)

                acc_test_tot.append(acc_test)
                print("Testing accuracy " + str(i) + ": {:.2f}".format(acc_test))
                if args.wandb:
                    wandb.log({
                        "Communication round": args.wu_epochs+iter,
                        "Local model " + str(i) + " test accuracy": acc_test
                    })
            if args.wandb:
                wandb.log({
                    "Communication round": args.wu_epochs+iter,
                    "Mean test accuracy": sum(acc_test_tot) / len(acc_test_tot)
                })
                                    
    # print(best_perf, 'AVG'+str(args.rs), sum(best_perf)/len(best_perf))
    torch.save(gen_w_glob, 'checkpoint/FedGAN' + str(args.name) + str(args.rs) + '.pt')

    if args.wandb:
        run.finish()


if __name__ == "__main__":
    for i in range(args.num_experiment):
        torch.manual_seed(args.rs)
        torch.cuda.manual_seed(args.rs)
        torch.cuda.manual_seed_all(args.rs) # if use multi-GPU
        np.random.seed(args.rs)
        random.seed(args.rs)
        main()
        args.rs = args.rs+1