from torch.utils.data import DataLoader, Dataset
from torch import nn
import torch
import torch.nn.functional as F
from torch.autograd import Variable
import numpy as np
from tqdm import tqdm
from utils.CGScore import pruning

class DatasetSplit(Dataset):
    def __init__(self, dataset, idxs):
        self.dataset = dataset
        self.idxs = list(idxs)

    def __len__(self):
        return len(self.idxs)

    def __getitem__(self, item):
        image, label = self.dataset[self.idxs[item]]
        return image, label


class FeatDataset(Dataset):
    def __init__(self, images, labels, transform=False):
        super(FeatDataset, self).__init__()
        self.images = images
        self.labels = labels
        self.transform = transform
        
    def __len__(self):
        return self.images.size(0)

    def __getitem__(self, idx):
        image = self.images[idx]
        if torch.is_tensor(self.labels[idx]):
            label = self.labels[idx].item()
        else:
            label = self.labels[idx]
        
        return image, label


class LocalUpdate_header_cg(object):
    def __init__(self, args, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True)
        self.iter = len(idxs)//args.local_bs
        self.gen_num = int(len(idxs)/(1-args.pruning_ratio))

    def train(self, net, learning_rate, gennet=None, cg=False, feature_extractor=None):
        net.train()
        feature_extractor.eval()

        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        gen_epoch_loss = None
        gen_loss = None
        if gennet:
            gen_epoch_loss = []
            gennet.eval()
    
            with torch.no_grad():
                images, labels = gennet.sample_image(self.args, sample_num=self.gen_num)
            
            labels = torch.argmax(labels, dim=1)
            syn_feat_dataset = FeatDataset(images, labels)
            if cg:
                syn_feat_dataset = pruning(syn_feat_dataset, self.args.pruning_ratio, self.args.device, self.args.from_low)
                sampler = torch.utils.data.RandomSampler(syn_feat_dataset, replacement=True)
                syn_feat_loader = torch.utils.data.DataLoader(syn_feat_dataset,
                                                                batch_size=self.args.local_bs,
                                                                sampler=sampler,
                                                                drop_last=True)
            else:
                syn_feat_loader = torch.utils.data.DataLoader(syn_feat_dataset,
                                                                batch_size=self.args.local_bs,
                                                                drop_last=True)
            # print('gen sample iteration',self.iter)
            for iter in range(self.args.local_ep_gen): # train by samples generated by generator
                gen_batch_loss = []
        
                for batch_idx, (images, labels) in enumerate(syn_feat_loader):
                    images, labels = images.to(self.args.device), labels.to(self.args.device)
                    net.zero_grad()
                    logits, log_probs = net(images, start_layer='feature')
                    loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

                    gen_batch_loss.append(loss.item())
                gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))     
            # gennet creates feature samples (gennet(, labels))
            if gen_epoch_loss:
                gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        epoch_loss = []

        for iter in range(self.args.local_ep): # train net performing main-task
            batch_loss = []

            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device)
                images = feature_extractor(images)
                net.zero_grad()
                logits, log_probs = net(images, start_layer = 'feature')
                loss = F.cross_entropy(logits, labels)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))
        if epoch_loss:
            avg_ep_loss = sum(epoch_loss) / len(epoch_loss)
        else:
            avg_ep_loss = -1
        
        return net.state_dict(), avg_ep_loss, gen_loss
    
    
class LocalUpdate_cg(object):
    def __init__(self, args, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True)
        self.iter = len(idxs)//args.local_bs
        self.gen_num = int(len(idxs)/(1-args.pruning_ratio))

    def train(self, net, learning_rate, gennet=None, cg=False, feature_start=True):
        net.train()

        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        gen_epoch_loss = None
        gen_loss = None
        if gennet:
            gen_epoch_loss = []
            gennet.eval()
            print(self.gen_num)

            images, labels = gennet.sample_image(self.args, sample_num=self.gen_num)
            labels = torch.argmax(labels, dim=1)
            syn_feat_dataset = FeatDataset(images, labels)
            if cg:
                syn_feat_dataset = pruning(syn_feat_dataset, self.args.pruning_ratio, self.args.device, self.args.from_low)
                # sampler = torch.utils.data.RandomSampler(syn_feat_dataset, replacement=True)
                syn_feat_loader = torch.utils.data.DataLoader(syn_feat_dataset,
                                                                batch_size=self.args.local_bs,
                                                                shuffle=True,
                                                                drop_last=True)
            else:
                syn_feat_loader = torch.utils.data.DataLoader(syn_feat_dataset,
                                                                batch_size=self.args.local_bs,
                                                                shuffle=True,
                                                                drop_last=True)
                
            # print('gen sample iteration',self.iter)
            for iter in range(self.args.local_ep_gen): # train by samples generated by generator
                gen_batch_loss = []
        
                for batch_idx, (images, labels) in enumerate(syn_feat_loader):
                    images, labels = images.to(self.args.device), labels.to(self.args.device)
                    net.zero_grad()
                    if feature_start:
                        logits, log_probs = net(images, start_layer='feature')
                    else:
                        logits, log_probs = net(images)
                    loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

                    gen_batch_loss.append(loss.item())
                gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))     
            # gennet creates feature samples (gennet(, labels))
            if gen_epoch_loss:
                gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        epoch_loss = []

        for iter in range(self.args.local_ep): # train net performing main-task
            batch_loss = []

            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device)
                net.zero_grad()
                logits, log_probs = net(images)
                loss = F.cross_entropy(logits, labels)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))

        return net.state_dict(), sum(epoch_loss) / len(epoch_loss), gen_loss


class LocalUpdate(object):
    def __init__(self, args, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True)
        self.iter = len(idxs)//args.local_bs
        
    def train(self, net, learning_rate, gennet=None, cg_pruning=False, feature_start=True):
        net.train()

        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        gen_epoch_loss = None
        gen_loss = None
        if gennet:
            gen_epoch_loss = []
            gennet.eval()
            # print('gen sample iteration',self.iter)
            for iter in range(self.args.local_ep_gen): # train by samples generated by generator
                gen_batch_loss = []
        
                for i in range(self.iter):
                    with torch.no_grad():
                        images, labels = gennet.sample_image(self.args) # images.shape (bs, feature^2)
                        
                    net.zero_grad()
                    if feature_start:
                        logits, log_probs = net(images, start_layer='feature')
                    else:
                        logits, log_probs = net(images)
                    loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

                    gen_batch_loss.append(loss.item())
                gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))     
            # gennet creates feature samples (gennet(, labels))
            if gen_epoch_loss:
                gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        epoch_loss = []

        for iter in range(self.args.local_ep): # train net performing main-task
            batch_loss = []

            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device) # labels = tensor([4,2,3,...], device='cuda:0')
                net.zero_grad()
                logits, log_probs = net(images)
                loss = F.cross_entropy(logits, labels)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))

        return net.state_dict(), sum(epoch_loss) / len(epoch_loss), gen_loss


class LocalUpdate_onlyGen(object):
    def __init__(self, args, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True)
        self.iter = len(idxs)//args.local_bs
        
    def train(self, net, learning_rate, gennet=None, feature_start=True):
        net.train()

        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        gen_epoch_loss = None
        gen_loss = None

        gen_epoch_loss = []
        gennet.eval()
        # print('gen sample iteration',self.iter)
        for iter in range(self.args.local_ep_gen): # train by samples generated by generator
            gen_batch_loss = []
    
            for i in range(self.iter):
                with torch.no_grad():
                    images, labels = gennet.sample_image(self.args) # images.shape (bs, feature^2)
                net.zero_grad()
                if feature_start: # commNet
                    logits, log_probs = net(images, start_layer='feature')
                else:
                    logits, log_probs = net(images)
                loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                gen_batch_loss.append(loss.item())
            gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))     
        # gennet creates feature samples (gennet(, labels))
        if gen_epoch_loss:
            gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

        return net.state_dict(), -1, gen_loss

    
class LocalUpdate_header(object):
    def __init__(self, args, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True)
        self.iter = len(idxs)//args.local_bs
        
    def train(self, net, learning_rate, gennet=None, feature_extractor=None):
        net.train()
        feature_extractor.eval()

        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        gen_epoch_loss = None
        gen_loss = None
        if gennet:
            gen_epoch_loss = []
            gennet.eval()
            # print('gen sample iteration',self.iter)
            for iter in range(self.args.local_ep_gen): # train by samples generated by generator
                gen_batch_loss = []
        
                for i in range(self.iter):
                    with torch.no_grad():
                        images, labels = gennet.sample_image(self.args) # images.shape (bs, feature^2)
                    net.zero_grad()
                    logits, log_probs = net(images, start_layer='feature')
                    loss = F.cross_entropy(logits, labels) # net.fc1.weight.grad / net.fc5.weight.grad
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

                    gen_batch_loss.append(loss.item())
                gen_epoch_loss.append(sum(gen_batch_loss)/len(gen_batch_loss))     
            # gennet creates feature samples (gennet(, labels))
            if gen_epoch_loss:
                gen_loss = sum(gen_epoch_loss) / len(gen_epoch_loss)

        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=learning_rate, momentum=self.args.momentum, weight_decay=self.args.weight_decay)
        epoch_loss = []

        for iter in range(self.args.local_ep): # train net performing main-task
            batch_loss = []

            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device)
                images = feature_extractor(images)
                net.zero_grad()
                logits, log_probs = net(images, start_layer = 'feature')
                loss = F.cross_entropy(logits, labels)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))
        if epoch_loss:
            avg_ep_loss = sum(epoch_loss) / len(epoch_loss)
        else:
            avg_ep_loss = -1
        
        return net.state_dict(), avg_ep_loss, gen_loss

def one_hot(labels, class_size):
    targets = torch.zeros(labels.size(0), class_size)
    for i, label in enumerate(labels):
        targets[i, label] = 1
    return targets

# Reconstruction + KL divergence losses summed over all elements and batch
def loss_function(recon_x, x, mu, logvar):
    BCE = F.binary_cross_entropy(recon_x, x, reduction='sum')
    # see Appendix B from VAE paper:
    # Kingma and Welling. Auto-Encoding Variational Bayes. ICLR, 2014
    # https://arxiv.org/abs/1312.6114
    # 0.5 * sum(1 + log(sigma^2) - mu^2 - sigma^2)
    KLD = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    return BCE + KLD

##############
# VAE
##############

LongTensor = torch.cuda.LongTensor
class LocalUpdate_VAE(object): # VAE
    def __init__(self, args, net_com, dataset=None, idxs=None):
        self.args = args
        self.selected_clients = []
        self.feature_extractor = net_com
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True, drop_last=True)

    def train(self, net):
        net.train()
        self.feature_extractor.eval()
        # train and update
        optimizer = torch.optim.Adam(net.parameters(), lr=1e-3)
        epoch_loss = []       

        for iter in range(self.args.gen_local_ep):
            batch_loss = []
            train_loss = 0
            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device) # images.shape: torch.Size([batch_size, 1, 28, 28])
                labels = Variable(labels.type(LongTensor))
                # labels = one_hot(labels, 10).to(self.args.device)
                with torch.no_grad():
                    images = self.feature_extractor(images) # x.view(-1, self.feature_size*self.feature_size) in CVAE.forward 
                # images = images.view(-1, 1, self.args.feature_size, self.args.feature_size) # self.args.local_bs
                # save_image(images.view(self.args.local_bs, 1, 14, 14),
                #             'imgFedCVAE/' + 'sample_' + '.png')
                                
                recon_batch, mu, logvar = net(images, labels)
                optimizer.zero_grad()
                loss = loss_function(recon_batch.view(-1, int(np.prod(self.args.img_shape))), images, mu, logvar)
                loss.backward()
                train_loss += loss.detach().cpu().numpy()
                optimizer.step()
                batch_loss.append(loss.item())

            # epoch_loss.append(sum(batch_loss)/len(batch_loss))
            epoch_loss.append(train_loss/len(self.ldr_train.dataset))            
        return net.state_dict(), sum(epoch_loss) / len(epoch_loss)


def loss_function_ccvae(x, pred, mu, logvar):
    recon_loss = F.mse_loss(pred, x, reduction='sum')
    kld = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())

    return recon_loss, kld

class LocalUpdate_CCVAE(object): # VAE
    def __init__(self, args, net_com, dataset=None, idxs=None):
        self.args = args
        self.selected_clients = []
        self.feature_extractor = net_com
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True, drop_last=True)

    def train(self, net):
        net.train()
        self.feature_extractor.eval()
        # train and update
        optimizer = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=0.001)

        epoch_loss = []       
        for iter in range(self.args.gen_local_ep):
            batch_loss = []
            train_loss = 0
            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images = images.to(self.args.device) # images.shape: torch.Size([batch_size, 1, 28, 28])
                images = self.feature_extractor(images) # x.view(-1, self.feature_size*self.feature_size) in CVAE.forward 
                label = np.zeros((images.shape[0], 10))
                label[np.arange(images.shape[0]), labels] = 1
                label = torch.tensor(label)
            
                # labels = one_hot(labels, 10).to(self.args.device)
                                
                # recon_batch, mu, logvar = net(images, labels)
                optimizer.zero_grad()
                pred, mu, logvar = net(images, label.to(self.args.device))
                
                recon_loss, kld = loss_function_ccvae(images, pred, mu, logvar)
                loss = recon_loss + kld
                loss.backward()
                optimizer.step()
                train_loss += loss.detach().cpu().numpy()
                batch_loss.append(loss.item())
            # epoch_loss.append(sum(batch_loss)/len(batch_loss))
            epoch_loss.append(train_loss/len(self.ldr_train.dataset))
        return net.state_dict(), sum(epoch_loss) / len(epoch_loss)
    

class LocalUpdate_DDPM(object): # DDPM
    def __init__(self, args, net_com, dataset=None, idxs=None):
        self.args = args
        self.selected_clients = []
        self.feature_extractor = net_com
        self.ldr_train = tqdm(DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True, drop_last=True))
        self.lr = 1e-4

    def train(self, net, lr_decay_rate):
        net.train()
        self.feature_extractor.eval()
        # train and update
        optim = torch.optim.Adam(net.parameters(), lr=1e-4)
        epoch_loss = []

        for iter in range(self.args.local_ep):
            optim.param_groups[0]['lr'] = self.lr*lr_decay_rate
            # (1-(self.args.local_ep*(round-1) + iter)/(self.args.local_ep*(self.args.epochs+self.args.wu_epochs)))
            loss_ema = None
            batch_loss = []
            train_loss = 0
            for images, labels in self.ldr_train:
                '''
                images, feature_extractor loss
                '''
                images = images.to(self.args.device) # images.shape: torch.Size([batch_size, 1, 28, 28])
                labels = labels.to(self.args.device)
                with torch.no_grad():
                    images = self.feature_extractor(images).view(-1, self.args.output_channel, self.args.img_size, self.args.img_size)
                # x.view(-1, self.feature_size*self.feature_size) in CVAE.forward 
                # images = images.view(-1, self.args.output_channel, self.args.img_size, self.args.img_size)
                # images = images.view(-1, 1, self.args.feature_size, self.args.feature_size) # self.args.local_bs
                # save_image(images.view(self.args.local_bs, 1, 14, 14),
                #             'imgFedCVAE/' + 'sample_' + '.png')
                optim.zero_grad()
                loss = net(images, labels)
                loss.backward()

                if loss_ema is None:
                    loss_ema = loss.item()
                else:
                    loss_ema = 0.95 * loss_ema + 0.05 * loss.item()                
                optim.step()

                batch_loss.append(loss_ema)
            epoch_loss.append(sum(batch_loss)/len(batch_loss))
        return net.state_dict(), sum(epoch_loss) / len(epoch_loss)
    

FloatTensor = torch.cuda.FloatTensor
# if cuda else torch.FloatTensor
LongTensor = torch.cuda.LongTensor # should be revised for different cuda id
# if cuda else torch.LongTensor
# img_shape = (1, 16, 16) # feature size
# img_shape = (1, 14, 14) # feature size

class LocalUpdate_GAN(object): # GAN
    def __init__(self, args, net_com, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.feature_extractor = net_com
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True, drop_last=True)
        
    def train(self, gnet, dnet):
        gnet.train()
        dnet.train()
        self.feature_extractor.eval()
        # train and update
        optimizerG = torch.optim.Adam(gnet.parameters(), lr=self.args.lr, betas=(self.args.b1, self.args.b2))
        optimizerD = torch.optim.Adam(dnet.parameters(), lr=self.args.lr, betas=(self.args.b1, self.args.b2))

        g_epoch_loss = []
        d_epoch_loss = []

        adversarial_loss = torch.nn.MSELoss()

        for iter in range(self.args.gen_local_ep):
            g_batch_loss = []
            # g_train_loss = 0
            d_batch_loss = []
            # d_train_loss = 0
            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                batch_size = images.shape[0]
                images = images.to(self.args.device)
                images = self.feature_extractor(images)
                
                # Adversarial ground truths
                valid = Variable(FloatTensor(batch_size, 1).fill_(1.0), requires_grad=False).to(self.args.device)
                fake = Variable(FloatTensor(batch_size, 1).fill_(0.0), requires_grad=False).to(self.args.device)

                # configure input
                real_imgs = Variable(images.type(FloatTensor)).to(self.args.device)
                labels = Variable(labels.type(LongTensor)).to(self.args.device)
                
                ''' -----------
                Train Generator
                ----------- '''
                optimizerG.zero_grad()
                
                # Sample noise and labels as generator input
                z = Variable(FloatTensor(np.random.normal(0, 1, (batch_size, self.args.latent_dim)))).to(self.args.device)
                gen_labels = Variable(LongTensor(np.random.randint(0, self.args.num_classes, batch_size))).to(self.args.device)
                
                # Generate a batch of images
                gen_imgs = gnet(z, gen_labels) # 196 (14*14)
                gen_imgs = gen_imgs.view(gen_imgs.size(0), *self.args.img_shape) # (1, 14, 14)
                
                # Loss measures generator's ability to fool the discriminator
                validity = dnet(gen_imgs, gen_labels)
                g_loss = adversarial_loss(validity, valid)
                
                g_loss.backward()
                optimizerG.step()
                
                ''' ---------------
                Train Discriminator
                ----------- '''
                optimizerD.zero_grad()
                
                # Loss for real images
                validity_real = dnet(real_imgs, labels)
                d_real_loss = adversarial_loss(validity_real, valid)
                # Loss for fake images
                validity_fake = dnet(gen_imgs.detach(), labels) # .detach()
                d_fake_loss = adversarial_loss(validity_fake, fake)
                
                d_loss = (d_real_loss + d_fake_loss) / 2
                
                d_loss.backward()
                optimizerD.step()
                
                # g_train_loss += g_loss.detach().cpu().numpy()
                # d_train_loss += d_loss.detach().cpu().numpy()
                g_batch_loss.append(g_loss.item())
                d_batch_loss.append(d_loss.item())                

            g_epoch_loss.append(sum(g_batch_loss)/len(g_batch_loss))
            d_epoch_loss.append(sum(d_batch_loss)/len(d_batch_loss))

        try:
            return gnet.state_dict(), dnet.state_dict(), sum(g_epoch_loss) / len(g_epoch_loss), sum(d_epoch_loss) / len(d_epoch_loss)
        except:
            return gnet.state_dict(), dnet.state_dict(), -1, -1

class LocalUpdate_DCGAN(object): # DCGAN
    def __init__(self, args, net_com, dataset=None, idxs=None):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        self.selected_clients = []
        self.feature_extractor = net_com
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=args.local_bs, shuffle=True, drop_last=True)
        
    def train(self, gnet, dnet, iter):
        gnet.train()
        dnet.train()
        self.feature_extractor.eval()

        # if iter==30:
        #     self.args.lr /= 10
        # elif iter==50:
        #     self.args.lr /= 10

        # train and update
        G_optimizer = torch.optim.Adam(gnet.parameters(), lr=self.args.lr, betas=(self.args.b1, self.args.b2))
        D_optimizer = torch.optim.Adam(dnet.parameters(), lr=self.args.lr, betas=(self.args.b1, self.args.b2))

        g_epoch_loss = []
        d_epoch_loss = []

        # adversarial_loss = torch.nn.MSELoss()
        BCE_loss = nn.BCELoss()

        # label preprocess
        onehot = torch.zeros(10, 10)
        img_size = self.args.img_shape[1]
        batch_size = self.args.local_bs

        onehot = onehot.scatter_(1, torch.LongTensor([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]).view(10,1), 1).view(10, 10, 1, 1) # 10 x 10 eye matrix
        fill = torch.zeros([10, 10, img_size, img_size])
        for i in range(10):
            fill[i, i, :, :] = 1

        y_real_ = torch.ones(batch_size)
        y_fake_ = torch.zeros(batch_size)
        y_real_, y_fake_ = Variable(y_real_.cuda()), Variable(y_fake_.cuda())
        y_real_, y_fake_ = y_real_.to(self.args.device), y_fake_.to(self.args.device)

        for iter in range(self.args.gen_local_ep):
            D_losses = []
            D_real_losses = []
            D_fake_losses = []
            G_losses = []

            if iter == 10:
                G_optimizer.param_groups[0]['lr'] /= 10
                D_optimizer.param_groups[0]['lr'] /= 10
                # print("learning rate change!")
                
            for batch_idx, (x_, y_) in enumerate(self.ldr_train):
                ''' ---------------------------------
                Train Discriminator
                maximize log(D(x)) + log(1 - D(G(z)))
                --------------------------------- '''
                dnet.zero_grad()

                mini_batch = x_.size()[0]

                if mini_batch != batch_size:
                    y_real_ = torch.ones(mini_batch)
                    y_fake_ = torch.zeros(mini_batch)
                    y_real_, y_fake_ = Variable(y_real_.cuda()), Variable(y_fake_.cuda())
                    y_real_, y_fake_ = y_real_.to(self.args.device), y_fake_.to(self.args.device)

                y_fill_ = fill[y_]
                x_, y_fill_ = Variable(x_.cuda()), Variable(y_fill_.cuda())
                x_, y_fill_ = x_.to(self.args.device), y_fill_.to(self.args.device)
                
                with torch.no_grad():
                    x_ = self.feature_extractor(x_)
                # save_image(x_.view(128, 1, 32, 32), 'imgFedDCGANF/' + 'sample_xx.png', nrow=10)
                
                D_result = dnet(x_, y_fill_).squeeze()
                D_real_loss = BCE_loss(D_result, y_real_)

                z_ = torch.randn((mini_batch, 100)).view(-1, 100, 1, 1)
                y_ = (torch.rand(mini_batch, 1) * 10).type(torch.LongTensor).squeeze()
                y_label_ = onehot[y_]
                y_fill_ = fill[y_]
                z_, y_label_, y_fill_ = Variable(z_.cuda()), Variable(y_label_.cuda()), Variable(y_fill_.cuda())
                z_, y_label_, y_fill_ = z_.to(self.args.device), y_label_.to(self.args.device), y_fill_.to(self.args.device)

                G_result = gnet(z_, y_label_)
                D_result = dnet(G_result, y_fill_).squeeze()

                D_fake_loss = BCE_loss(D_result, y_fake_)
                D_fake_score = D_result.data.mean()

                D_train_loss = D_real_loss + D_fake_loss
                D_real_losses.append(D_real_loss)
                D_fake_losses.append(D_fake_loss)

                D_train_loss.backward()
                D_optimizer.step()

                D_losses.append(D_train_loss.data)

                ''' -------------------
                Train Generator
                maximize log(D(G(z)))
                ------------------- '''
                gnet.zero_grad()

                z_ = torch.randn((mini_batch, 100)).view(-1, 100, 1, 1)
                y_ = (torch.rand(mini_batch, 1) * 10).type(torch.LongTensor).squeeze()
                y_label_ = onehot[y_]
                y_fill_ = fill[y_]
                z_, y_label_, y_fill_ = Variable(z_.cuda()), Variable(y_label_.cuda()), Variable(y_fill_.cuda())
                z_, y_label_, y_fill_ = z_.to(self.args.device), y_label_.to(self.args.device), y_fill_.to(self.args.device)

                G_result = gnet(z_, y_label_)
                D_result = dnet(G_result, y_fill_).squeeze()

                G_train_loss = BCE_loss(D_result, y_real_)

                G_train_loss.backward()
                G_optimizer.step()

                G_losses.append(G_train_loss.data)
        
            g_epoch_loss.append(sum(G_losses)/len(G_losses))
            d_epoch_loss.append(sum(D_losses)/len(D_losses))
            # print('Real loss {:4f}, Fake loss{:4f}'.format(sum(D_real_losses)/len(D_real_losses), sum(D_fake_losses)/len(D_fake_losses)))
                                
        try:
            return gnet.state_dict(), dnet.state_dict(), sum(g_epoch_loss) / len(g_epoch_loss), sum(d_epoch_loss) / len(d_epoch_loss)
        except:
            return gnet.state_dict(), dnet.state_dict(), -1, -1