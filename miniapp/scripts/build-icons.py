"""Render the project's simple line icon set (requires Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw
import math
OUT = Path(__file__).resolve().parents[1] / 'src-checkin/assets'
OUT.mkdir(parents=True, exist_ok=True)
S = 12

def render(kind, color, name):
    image = Image.new('RGBA', (24*S,24*S))
    draw = ImageDraw.Draw(image)
    def line(points):
        pts=[(int(x*S),int(y*S)) for x,y in points]
        draw.line(pts,fill=color,width=2*S,joint='curve')
        for x,y in (pts[0],pts[-1]): draw.ellipse((x-S,y-S,x+S,y+S),fill=color)
    def circle(box): draw.ellipse(tuple(int(v*S) for v in box),outline=color,width=2*S)
    if kind == 'home':
        line([(3,10),(12,3),(21,10)]); line([(5,9),(5,21),(10,21),(10,15),(14,15),(14,21),(19,21),(19,9)])
    elif kind == 'accounts':
        for x,y in [(4,4),(14,4),(4,14),(14,14)]:
            draw.rounded_rectangle((x*S,y*S,(x+6)*S,(y+6)*S),radius=S,outline=color,width=2*S)
    elif kind == 'history':
        circle((3,3,21,21)); line([(12,7),(12,12),(16,14)])
    elif kind == 'mine':
        circle((8,3,16,11)); line([(4,21),(4,19),(5,16),(8,14),(16,14),(19,16),(20,19),(20,21)])
    elif kind == 'app':
        draw.rounded_rectangle((5*S,2*S,19*S,22*S),radius=2*S,outline=color,width=2*S); line([(10,18),(14,18)])
    elif kind == 'mini':
        circle((3,5,15,17)); circle((9,7,21,19)); line([(8,12),(16,12)])
    elif kind == 'empty':
        draw.rounded_rectangle((5*S,3*S,19*S,21*S),radius=2*S,outline=color,width=2*S); line([(9,8),(15,8)]); line([(9,12),(15,12)]); line([(9,16),(12,16)])
    image.resize((96,96),Image.Resampling.LANCZOS).save(OUT / (name+'.png'))
for kind in ['home','accounts','history','mine']:
    render(kind,'#61727b',kind); render(kind,'#107f78',kind+'-active')
render('app','#346ab0','app'); render('mini','#107f78','mini'); render('empty','#91b9b3','empty')
print('11 icons rendered')
