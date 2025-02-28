from fastapi import FastAPI, File, UploadFile, Form
import torchvision.transforms as tf
import argparse
import os
import sys
import math
import pickle
from PIL import Image
import numpy as np
import cv2
import collections
from sklearn.svm import SVC
import base64
from facenet_pytorch import MTCNN, InceptionResnetV1, fixed_image_standardization, training
import torch
from torch.utils.data import DataLoader, SubsetRandomSampler, Dataset
from torch import optim
from torch.optim.lr_scheduler import MultiStepLR
from torchvision import datasets, transforms
import numpy as np
import pandas as pd
import os
from io import BytesIO
from pydantic import BaseModel
from glob import glob
import shutil
import warnings
from torch.utils.tensorboard import SummaryWriter

warnings.filterwarnings('ignore')

WORKING_PATH = os.environ['dir']
MODEL_PATH = WORKING_PATH + '/source/models/train_model.pt'
CURRENT_MODEL = WORKING_PATH + '/source/models/current_model.pt'
DATABASE_PATH = WORKING_PATH + '/database'
data_dir = DATABASE_PATH + '/test_images'
data_cropped = data_dir + '_cropped'
bins_dir = DATABASE_PATH + '/bins'
batch_size = 32
epochs = 8
workers = 0 if os.name == 'nt' else 8
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
mtcnn = MTCNN(
    image_size=160, margin=0, min_face_size=20,
    thresholds=[0.6, 0.7, 0.7], factor=0.709, post_process=True,
    device=device
)
loss_fn = torch.nn.CrossEntropyLoss()
metrics = {
    'fps': training.BatchTimer(),
    'acc': training.accuracy
}

class CustomDataset(Dataset):
    def __init__(self, path=data_cropped):
        super().__init__()
        self.paths = glob(path + "/*/*")
        self.num_classes = len(glob(path + "/*"))
        

    def __getitem__(self, idx):
        path = self.paths[idx]
        data = Image.open(path)
        label = int(path.split('/')[-1][0])
        return data, label

    def __len__(self):
        return len(self.paths)
        


def prepare_data():
    dataset = datasets.ImageFolder(data_dir)
    dataset.samples = [
        (p, p.replace(data_dir, data_cropped))
            for p, _ in dataset.samples
    ]

    loader = DataLoader(
        dataset,
        num_workers=workers,
        batch_size=1,
        collate_fn = training.collate_pil
    )

    for i, (x, y) in enumerate(loader):
        mtcnn(x, save_path=y)
        print('\rBatch {} of {}'.format(i + 1, len(loader)), end='')
    
def train(model, train_loader, val_loader, optimizer, scheduler, metrics):
    
    writer = SummaryWriter()
    writer.iteration, writer.interval = 0, 10
    for epoch in range(8):

        print('\nEpoch {}/{}'.format(epoch + 1, epochs))
        print('-' * 10)
        model.train()
        training.pass_epoch(
            model, loss_fn, train_loader, optimizer, scheduler,
            batch_metrics=metrics, show_running=True, device=device,
            writer=writer
        )

        model.eval()
        training.pass_epoch(
            model, loss_fn, val_loader,
            batch_metrics=metrics, show_running=True, device=device,
            writer=writer
        )
    writer.close()


class dbController:
    def __init__(self, num_classes, class_name):
        self.num_classes = num_classes
        self.class_name = class_name
        self.class_dict = dict([(i, class_name[i]) for i in range(num_classes)])
    def addRegistration(self, files, name, id):
        #get the id
        overlap = False 
        self.num_classes += 1 
        self.class_name.append(name)
        self.class_dict = dict([(i, self.class_name[i]) for i in range(self.num_classes)])
        #folder for storage
        if not os.path.exists(f'{data_dir}/{name}_{id}'):
            os.mkdir(f'{data_dir}/{name}_{id}')
            overlap = True
        for i, file in enumerate(files):
            with open(f'{data_dir}/{name}_{id}/{i+1}.jpg', 'wb') as f:
                f.write(file.getbuffer())
        return overlap
    def deleteRegister(self, name, id):
        '''
        rm -rf in test-images and mv to bins
        '''
        exist = True
        if os.path.exists(f'{data_dir}/{name}_{id}'):
            exist = False
            idx = None
            os.system(f"rm -rf {data_dir}/{name}_{id}")
            os.system(f"touch {bins_dir}/{name}_{id}.txt")
            self.num_classes -= 1
            #index
            for i in range(len(self.class_name)):
                if self.class_name[i] == name + '_' + id: 
                    idx = i
                    break
            self.class_name.pop(idx)
            self.class_dict = dict([(i, self.class_name[i]) for i in range(self.num_classes)])

        return exist
    def deleteBins(self, filename):
        os.system(f"rm -rf {data_cropped}/{filename}")
        os.system(f"rm -rf {bins_dir}/{filename}.txt")
    def fit(self):
        prepare_data()
        trans = transforms.Compose([
            np.float32,
            transforms.ToTensor(),
            fixed_image_standardization
        ])
        dataset = datasets.ImageFolder(data_cropped, transform=trans)
        # dataset = CustomDataset(data_cropped)
        img_inds = np.arange(len(dataset))
        np.random.shuffle(img_inds)
        train_inds = img_inds[:int(0.8 * len(img_inds))]
        val_inds = img_inds[int(0.8 * len(img_inds)):]
        #model init
        resnet = InceptionResnetV1(
        classify=True,
        pretrained='vggface2',
        num_classes=len(dataset.class_to_idx)
        ).to(device)
        for params in resnet.parameters():
            params.requires_grad_(False)
        for params in resnet.logits.parameters():
            params.requires_grad_(True)


        optimizer = optim.Adam(resnet.parameters(), lr=0.001)
        scheduler = MultiStepLR(optimizer, [5, 10])




        train_loader = DataLoader(
            dataset,
            num_workers=workers,
            batch_size=32,
            sampler=SubsetRandomSampler(train_inds)
        )

        val_loader = DataLoader(
            dataset,
            num_workers=workers,
            batch_size=32,
            sampler=SubsetRandomSampler(val_inds)
        )
        train(resnet, train_loader, val_loader, optimizer, scheduler, metrics)
        torch.save(resnet.state_dict(), MODEL_PATH)
        shutil.move(MODEL_PATH, CURRENT_MODEL)

if __name__ == '__main__':
    paths = glob(DATABASE_PATH + '/test_images/*')
    num_classes = len(paths)
    print("NUM CLASSES: ", num_classes)
    class_name = [path.split('/')[-1] for path in paths]
    controller = dbController(num_classes, class_name)
    # controller.fit()
    controller.deleteRegister(name='neymar', id='2002')
    print("deleted", controller.num_classes)
    print("class", controller.class_name)