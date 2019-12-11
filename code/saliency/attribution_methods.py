import torch 
import torch.nn as nn
from torch.autograd import Variable

import numpy as np
import h5py
from tqdm import tqdm

import cv2
from PIL import Image

from collections import OrderedDict
from functools import partial

from utils import rescale_image
from model import SimpleCNNDeconv



class VanillaBackprop(object):
    def __init__(self, model):
        self.model = model 
        # evaluation mode
        self.model.eval()

    def generate_image(self, pre_imgs, targets, **kwargs):
        pre_imgs = Variable(pre_imgs, requires_grad=True)
        outputs = self.model(pre_imgs)

        self.model.zero_grad()

        one_hot_output = torch.zeros_like(outputs).scatter(1, targets.unsqueeze(1), 1).detach()
        outputs.backward(gradient=one_hot_output)
        probs, preds = outputs.detach().max(1)

        sal_maps = rescale_image(pre_imgs.grad.numpy())

        return (sal_maps, probs.numpy(), preds.numpy())
    
    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')


class IntegratedGradients(object):
    def __init__(self, model):
        self.model = model
        # evaluation mode
        self.model.eval()

    def generate_images_on_linear_path(self, input_images, steps):
        step_list = np.arange(steps+1)/steps
        xbar_list = [input_images*step for step in step_list]
        return xbar_list 

    def generate_gradients(self, pre_imgs, targets, **kwargs):
        pre_imgs = Variable(pre_imgs, requires_grad=True)
        outputs = self.model(pre_imgs)
        
        self.model.zero_grad()

        one_hot_output = torch.zeros_like(outputs).scatter(1, targets.unsqueeze(1), 1).detach()
        outputs.backward(gradient=one_hot_output)
        probs, preds = outputs.detach().max(1)

        imgs = pre_imgs.grad.numpy()

        return (imgs, probs.numpy(), preds.numpy())

    def generate_image(self, pre_imgs, targets, **kwargs):
        # default
        steps = 10 if 'steps' not in kwargs.keys() else kwargs['steps']

        xbar_list = self.generate_images_on_linear_path(pre_imgs, steps)
        sal_maps = np.zeros(pre_imgs.size())

        for xbar_image in xbar_list:
            single_integrated_grad, probs, preds = self.generate_gradients(xbar_image, targets)
            sal_maps = sal_maps + (single_integrated_grad/steps)
            
        sal_maps = rescale_image(sal_maps)
        
        return (sal_maps, probs, preds)

    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')


class GuidedBackprop(object):
    def __init__(self, model):
        self.model = model    
        # evaluation mode
        self.model.eval()
        # update relu
        self.forward_relu_outputs = []
        self.update_relus()

    def update_relus(self):
        def relu_backward_hook_function(module, grad_in, grad_out):
            corresponding_forward_output = self.forward_relu_outputs[-1]
            corresponding_forward_output[corresponding_forward_output > 0] = 1
            modified_grad_out = corresponding_forward_output * torch.clamp(grad_in[0], min=0.0)
            del self.forward_relu_outputs[-1]

            return (modified_grad_out,)

        def relu_forward_hook_function(module, ten_in, ten_out):
            self.forward_relu_outputs.append(ten_out)

        for pos, module in self.model.features._modules.items():
            if isinstance(module, nn.ReLU):
                module.register_backward_hook(relu_backward_hook_function)
                module.register_forward_hook(relu_forward_hook_function)

    def generate_image(self, pre_imgs, targets, **kwargs):
        pre_imgs = Variable(pre_imgs, requires_grad=True)
        outputs = self.model(pre_imgs)

        self.model.zero_grad()
        
        one_hot_output = torch.zeros_like(outputs).scatter(1, targets.unsqueeze(1), 1).detach()
        outputs.backward(gradient=one_hot_output)
        probs, preds = outputs.detach().max(1)

        sal_maps = rescale_image(pre_imgs.grad.numpy())

        return (sal_maps, probs.numpy(), preds.numpy())

    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')

class GradCAM(object):
    def __init__(self, model):
        self.model = model
        # evaluation mode
        self.model.eval()
        # hook
        self.hook_layers()
        # initial dicts
        self.conv_outputs = OrderedDict()
        self.gradients = OrderedDict()
    
    def hook_layers(self):
        def hook_forward(module, input, output, key):
            if isinstance(module, torch.nn.MaxPool2d):
                self.conv_outputs[key] = output[0]
            else:
                self.conv_outputs[key] = output

        def hook_backward(module, input, output, key):
            self.gradients[key] = output[0]

        for idx, layer in enumerate(self.model._modules.get('features')):
            layer.register_forward_hook(partial(hook_forward, key=idx))
            layer.register_backward_hook(partial(hook_backward, key=idx))
            
    def generate_image(self, pre_imgs, targets, **kwargs):
        # default
        layer = 8 if 'layer' not in kwargs.keys() else kwargs['layer']
        color = False if 'color' not in kwargs.keys() else kwargs['color']

        pre_imgs = Variable(pre_imgs, requires_grad=True)
        outputs = self.model(pre_imgs)

        self.model.zero_grad()

        one_hot_output = torch.zeros_like(outputs).scatter(1, targets.unsqueeze(1), 1).detach()
        outputs.backward(gradient=one_hot_output)
        probs, preds = outputs.detach().max(1)

        gradients = self.gradients[layer].numpy()
        
        # A = w * conv_output
        convs = self.conv_outputs[layer].detach().numpy()
        weights = np.mean(gradients, axis=(2,3))
        weights = weights.reshape(weights.shape + (1,1,))

        gradcams = weights * convs
        gradcams = gradcams.sum(axis=1)

        # relu
        gradcams = np.maximum(gradcams, 0)

        # minmax scaling * 255
        mins = gradcams.min(axis=(1,2))
        mins = mins.reshape(mins.shape + (1,1,))
        maxs = gradcams.max(axis=(1,2))
        maxs = maxs.reshape(maxs.shape + (1,1,))

        gradcams = (gradcams - mins)/(maxs - mins)
        gradcams = np.uint8(gradcams * 255)
    
        # resize to input image size
        def resize_image(gradcam, origin_image, color):
            img = np.uint8(Image.fromarray(gradcam).resize((origin_image.shape[-2:]), Image.ANTIALIAS))/255
            if color:
                # output_GC (H,W) to (H,W,C)
                img = cv2.applyColorMap(np.uint8(img*255), cv2.COLORMAP_JET)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            else:
                img = np.expand_dims(img, axis=2)
            
            return img

        colors = [color] * gradcams.shape[0]
        gradcams = np.array(list(map(resize_image, gradcams, pre_imgs, colors)))

        return (gradcams, probs.numpy(), preds.numpy())

    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')


class DeconvNet(object):
    def __init__(self, model, deconv_model):
        self.model = model
        self.deconv_model = deconv_model

        # evalution mode
        self.model.eval()
        # hook
        self.hook_layers()

    def hook_layers(self):
        def hook(module, input, output, key):
            if isinstance(module, torch.nn.MaxPool2d):
                self.model.feature_maps[key] = output[0]
                self.model.pool_locs[key] = output[1]
            else:
                self.model.feature_maps[key] = output

        for idx, layer in enumerate(self.model._modules.get('features')):
            layer.register_forward_hook(partial(hook, key=idx))

    def generate_image(self, pre_imgs, targets, **kwargs):
        # default
        layer = 0 if 'layer' not in kwargs.keys() else kwargs['layer']

        # prediction
        outputs = self.model(pre_imgs).detach()
        probs, preds = outputs.max(1)
        
        # feature size
        num_feat = self.model.feature_maps[layer].shape[1]
        new_feat_map = self.model.feature_maps[layer].clone()

        # output deconvnet
        deconv_outputs = self.deconv_model(self.model.feature_maps[layer], layer, self.model.pool_locs)

        # denormalization
        deconv_outputs = deconv_outputs.data.numpy()
        deconv_outputs = rescale_image(deconv_outputs)
        
        return (deconv_outputs, probs.numpy(), preds.numpy())
    
    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')

class GuidedGradCAM(object):
    def __init__(self, model):
        self.GC_model = GradCAM(model)
        self.GB_model = GuidedBackprop(model)

    def generate_image(self, pre_imgs, targets, **kwargs):
        # default
        layer = 8 if 'layer' not in kwargs.keys() else kwargs['layer']        
        color = False if 'color' not in kwargs.keys() else kwargs['color']

        output_GC, probs, preds = self.GC_model.generate_image(pre_imgs, targets, layer=layer, color=color)
        output_GB, _, _ = self.GB_model.generate_image(pre_imgs, targets)

        sal_maps = np.multiply(output_GC, output_GB)
        sal_maps = rescale_image(sal_maps.transpose(0,3,1,2))

        return sal_maps, probs, preds

    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')

    
class InputBackprop(object):
    def __init__(self, model):
        self.VBP_model = VanillaBackprop(model)

    def generate_image(self, pre_imgs, targets, **kwargs):
        output_VBP, probs, preds = self.VBP_model.generate_image(pre_imgs, targets, **kwargs)
        input_img = pre_imgs.detach().numpy()
        input_img = rescale_image(input_img)

        sal_maps = np.multiply(output_VBP, input_img)
        sal_maps = rescale_image(sal_maps.transpose(0,3,1,2))

        return sal_maps, probs, preds

    def save_saliency_map(self, dataloader, save_dir, **kwargs):
        img_size = dataloader.dataset.data.shape[1:] 
        dim = len(img_size)
        if dim == 2:
            img_size = img_size + (1,)
        sal_maps = np.array([], dtype=np.float32).reshape((0,) + img_size)
        probs = np.array([], dtype=np.float32)
        preds = np.array([], dtype=np.uint8)
        for img_b, target_b in tqdm(dataloader, desc='Vanilla Backprop'):
            sal_map_b, prob_b, pred_b = self.generate_image(img_b, target_b, **kwargs)
            sal_maps = np.vstack([sal_maps, sal_map_b])
            probs = np.append(probs, prob_b)
            preds = np.append(preds, pred_b)

        with h5py.File(save_dir, 'w') as hf:
            hf.create_dataset('saliencys',data=sal_maps)
            hf.create_dataset('probs',data=probs)
            hf.create_dataset('preds',data=preds)
            hf.close()
        print('Save saliency maps')