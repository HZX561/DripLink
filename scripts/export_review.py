"""Export a numbered JPEG comparison sheet and a plain-text review form."""
import argparse
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from watermeter.data import DATA,load_manifest,image_path,read_json,write_json
from watermeter.runtime import ROOT


def export_examples(report,output,count=6):
    """Show correct, integer-error and fractional-error examples from distinct groups."""
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    if count<3:raise ValueError('Use at least three examples to show all categories')
    manifest=load_manifest();lookup={r['name']:r for r in manifest['records']}
    report=Path(report);evaluation=read_json(report/'report.json')
    rows=read_json(report/'predictions.json')
    categories=('correct','integer_error','fraction_only')
    names={'correct':'完整读数一致','integer_error':'整数读数不一致','fraction_only':'仅小数部分不一致'}
    colors={'correct':'#16734B','integer_error':'#C0392B','fraction_only':'#AD6500'}
    buckets={key:[] for key in categories}
    for row in rows:
        prediction=row.get('automatic_context')
        if not prediction:continue
        actual=prediction['scaled_value'];target=row['target_scaled']
        category='correct' if actual==target else 'fraction_only' if actual//10000==target//10000 else 'integer_error'
        buckets[category].append(row)
    selected=[];groups=set()
    # Preserve validation order within each category; avoid repeating near-duplicate groups.
    for round_index in range((count+2)//3):
        for category in categories:
            if len(selected)>=count:break
            candidate=next((r for r in buckets[category] if lookup[r['name']]['group'] not in groups),None)
            if candidate is None:continue
            groups.add(lookup[candidate['name']]['group']);selected.append((category,candidate))
    if len(selected)<count:raise ValueError('Not enough distinct groups for this example count')
    output.mkdir(parents=True)
    chinese=Path('C:/Windows/Fonts/msyh.ttc');latin=Path('C:/Windows/Fonts/arial.ttf')
    font_path=chinese if chinese.exists() else latin
    def font(size):return ImageFont.truetype(str(font_path),size)
    heading,body,small=font(25),font(25),font(19)
    card_w,card_h=540,600;columns=3;header=140
    sheet=Image.new('RGB',(columns*card_w,header+((len(selected)+2)//3)*card_h),'#EDF1F5')
    draw=ImageDraw.Draw(sheet)
    draw.text((26,18),'水表识别：模型预测与数据集 GT',font=font(35),fill='#172B4D')
    draw.text((26,72),f'验证集示例 · 单位 m³ · GT 来自数据集标注，尚未人工复核',font=font(22),fill='#475569')
    draw.text((26,106),'每类展示不同近重复组的样本；示例比例不代表整体准确率。',font=small,fill='#64748B')
    sources=[];lines=[]
    def reading(value):return f'{value//10000}.{value%10000:04d}'
    for number,(category,row) in enumerate(selected,1):
        record=lookup[row['name']];source=image_path(record,DATA)
        card=Image.new('RGB',(card_w,card_h),'white');d=ImageDraw.Draw(card)
        d.rectangle((0,0,card_w-1,card_h-1),outline='#D7DFE8',width=2)
        d.text((18,13),f'{number:02d}  {names[category]}',font=heading,fill=colors[category])
        with Image.open(source) as original:
            photo=original.convert('RGB');photo.thumbnail((500,375))
            # Resize for display only, preserving all original image content and aspect ratio.
            ratio=min(500/photo.width,375/photo.height)
            if ratio>1:photo=photo.resize((round(photo.width*ratio),round(photo.height*ratio)),Image.Resampling.LANCZOS)
        d.rectangle((18,62,522,439),fill='#F3F5F7')
        card.paste(photo,((card_w-photo.width)//2,62+(375-photo.height)//2))
        prediction=row['automatic_context']['scaled_value'];target=row['target_scaled']
        p,t=reading(prediction),reading(target)
        d.text((20,454),f'预测：{p} m³',font=body,fill=colors[category])
        d.text((20,494),f'GT：   {t} m³',font=body,fill='#172B4D')
        error=reading(abs(prediction-target))
        d.text((20,540),f'绝对误差：{error} m³',font=small,fill='#475569')
        d.text((20,570),f'验证集 · 图像组 {record["group"]}',font=small,fill='#64748B')
        filename=f'{number:02d}_{category}.jpg';card.save(output/filename,quality=96,subsampling=0)
        index=number-1;sheet.paste(card,((index%columns)*card_w,header+(index//columns)*card_h))
        sources.append({'number':number,'category':category,'name':row['name'],'image':f'data/images/{row["name"]}',
                        'image_sha256':record['sha256'],'group':record['group'],'prediction_m3':p,'gt_m3':t,
                        'original_gt_label':row['target_reading'],'absolute_error_m3':error,'visualization':filename})
        lines.append(f'{number:02d} {names[category]}\n预测={p} m³\nGT={t} m³\n原图={source}\n')
    sheet.save(output/'prediction_vs_gt.jpg',quality=96,subsampling=0)
    write_json(output/'examples.json',{'split':evaluation['split'],'prediction_source':str(report/'predictions.json'),
        'manifest_sha256':evaluation['manifest_sha256'],'detector_sha256':evaluation['detector_sha256'],
        'reader_sha256':evaluation['reader_sha256'],'gt_source':'Dataset-provided labels, not newly human verified',
        'selection':'First examples per category in validation order, one example per near-duplicate group',
        'examples':sources})
    (output/'readings.txt').write_text('GT 来自冻结数据集标签，不是根据文件名推测；统一显示四位小数。\n\n'+'\n'.join(lines),encoding='utf-8-sig')
    print(output.resolve())


def export(report,output,count=16):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    if count<1:raise ValueError('Count must be positive')
    manifest=load_manifest();lookup={r['name']:r for r in manifest['records']}
    rows=read_json(Path(report)/'predictions.json')
    def error(row):
        p=row.get('automatic_context');return abs(p['scaled_value']-row['target_scaled']) if p else float('inf')
    rows=sorted(rows,key=error,reverse=True)[:count];output.mkdir(parents=True)
    font_path=Path('C:/Windows/Fonts/arial.ttf')
    font=ImageFont.truetype(str(font_path),17) if font_path.exists() else ImageFont.load_default()
    sheet=Image.new('RGB',(4*320,((len(rows)+3)//4)*360),'white');text=[];sources=[]
    for i,row in enumerate(rows,1):
        source=image_path(lookup[row['name']],DATA);name=f'{i:02d}{source.suffix}'
        shutil.copy2(source,output/name);image=Image.open(source).convert('RGB');image.thumbnail((310,285))
        x,y=((i-1)%4)*320,((i-1)//4)*360;sheet.paste(image,(x+(320-image.width)//2,y))
        prediction=row.get('automatic_context',{}).get('reading','FAILED');target=row['target_reading']
        draw=ImageDraw.Draw(sheet);draw.text((x+6,y+290),f'{i:02d} Pred: {prediction}',font=font,fill='black')
        draw.text((x+6,y+315),f'Label: {target}',font=font,fill='black')
        text.append(f'{i:02d} 照片={name} 预测={prediction} 数据集标注={target}\n人工读数=    核验人=    备注=\n')
        sources.append({'number':i,'name':row['name'],'prediction':prediction,'dataset_label':target})
    sheet.save(output/'comparison.jpg',quality=93)
    (output/'review.txt').write_text('数据集标注不是本次人工核验真值；请结合原图核对。\n\n'+'\n'.join(text),encoding='utf-8-sig')
    write_json(output/'sources.json',sources);print(output.resolve())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=ROOT/'reports/validation');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['errors','examples'],default='errors')
    parser.add_argument('--count',type=int);args=parser.parse_args()
    if args.mode=='examples':export_examples(args.report,args.output,args.count or 6)
    else:export(args.report,args.output,args.count or 16)
