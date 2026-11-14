"""The selected 953,194-parameter pointer reader; state-dict keys stay unchanged."""
import torch
from torch import nn


class VisualStem(nn.Module):
    def __init__(self):
        super().__init__();layers=[];incoming=1
        for channels in (24,48,96,160):
            layers += [nn.Conv2d(incoming,channels,3,2,1,bias=False),nn.BatchNorm2d(channels),nn.SiLU(),
                       nn.Conv2d(channels,channels,3,1,1,bias=False),nn.BatchNorm2d(channels),nn.SiLU()]
            incoming=channels
        self.layers=nn.Sequential(*layers)

    def forward(self,images):return self.layers(images)


class ReadingNetwork(nn.Module):
    def __init__(self,hidden=128,layers=2,digit_head=True,backbone='tiny',phase_head=False,pointer_only=True):
        super().__init__()
        if not pointer_only or not digit_head or backbone!='tiny' or phase_head:
            raise ValueError('Only the selected full-pointer architecture is supported')
        self.config=dict(hidden=hidden,layers=layers,digit_head=True,backbone='tiny',phase_head=False,pointer_only=True)
        self.pointer_stem=VisualStem()
        self.pointer_embedding=nn.Sequential(nn.AdaptiveAvgPool2d((2,2)),nn.Flatten(),nn.Linear(640,hidden),nn.SiLU())
        self.pointer_states=nn.Linear(hidden,30)
        self.independent_digits=nn.Linear(hidden,10)
        self.position=nn.Embedding(10,hidden);self.presence=nn.Embedding(3,hidden)
        self.probability_embedding=nn.Linear(10,hidden)
        layer=nn.TransformerEncoderLayer(hidden,4,hidden*3,.1,batch_first=True,norm_first=True)
        self.context=nn.TransformerEncoder(layer,layers,enable_nested_tensor=False)
        self.correction=nn.Linear(hidden,10)
        nn.init.zeros_(self.correction.weight);nn.init.zeros_(self.correction.bias)
        mapping=torch.zeros(30,10)
        for state in range(30):mapping[state,(state-11)%10 if 10<=state<20 else state%10]=1
        self.register_buffer('state_to_digit',mapping)  # Retained checkpoint buffer, not a wheel branch.

    def forward(self,patches,mask):
        batch=patches.shape[0]
        features=patches.new_zeros((batch,10,self.config['hidden']))
        states=patches.new_zeros((batch,10,30));single=patches.new_zeros((batch,10,10))
        if mask.any():
            visual=self.pointer_embedding(self.pointer_stem(patches[mask].unsqueeze(1)))
            features[mask]=visual.float();states[mask]=self.pointer_states(visual).float()
            single[mask]=self.independent_digits(visual).float()
        probabilities=single.softmax(-1)
        zero_prior=torch.full_like(probabilities,1e-4);zero_prior[:,:,0]=1-9e-4
        probabilities=torch.where(mask[:,:,None],probabilities,zero_prior)
        baseline=probabilities.clamp_min(1e-5).log()
        embedded=features+self.position.weight[None]+self.presence(mask.long())+self.probability_embedding(probabilities)
        return {'logits':baseline+self.correction(self.context(embedded)),
                'base_logits':baseline,'states':states,'single_logits':single}


def load_reader(path,device='cpu'):
    checkpoint=torch.load(path,map_location='cpu',weights_only=True)
    model=ReadingNetwork(**checkpoint['config']);model.load_state_dict(checkpoint['model'],strict=True)
    return model.to(device).eval(),checkpoint
