"""Bilingual intent expansion and evidence-based catalogue ranking, without an API key."""
import re

# User phrases and catalogue terminology share the same concept, not necessarily
# the same spelling. Broad concepts do not imply every specialised capability.
CONCEPTS = {
 '视频 / Video': ['视频', 'video', '视频处理'],
 '分镜 / Storyboard': ['分镜', '故事板', 'storyboard', 'storyboarding', 'shot planning', '镜头规划', '分镜脚本'],
 '放大 / Upscale': ['放大', '超分', '变清晰', '高清化', '提高清晰度', 'upscale', 'upscaler', 'upscaling', 'super resolution'],
 '动画 / Animation': ['动画', '动起来', 'animation', 'animatediff', 'animate', 'animating'],
 '图生视频 / Image to video': ['图生视频', '图片变成视频', '照片变成视频', '图片动起来', '图片变成动画', 'image to video', 'image2video', 'i2v'],
 '文生视频 / Text to video': ['文生视频', '文字生成视频', 'text to video', 'text2video', 't2v'],
 '补帧 / Frame interpolation': ['补帧', '插帧', '更流畅', '提高帧率', 'frame interpolation', 'interpolate frames', 'rife', 'film interpolation'],
 '蒙版 / Mask': ['蒙版', '遮罩', 'mask', 'masking', 'segmentation', 'segment anything'],
 '抠图 / Background removal': ['抠图', '去背景', '去掉背景', '去除背景', '删除背景', '移除背景', '背景去掉', 'matting', 'rembg', 'background removal', 'remove background', 'birefnet'],
 '局部重绘 / Inpaint': ['局部重绘', '局部修改', 'inpaint', 'inpainting'],
 '扩图 / Outpaint': ['扩图', '扩展画面', 'outpaint', 'outpainting'],
 '修复 / Restoration': ['修复', '老照片', '去噪', 'restoration', 'restore', 'denoise', 'denoising'],
 '换脸 / Face swap': ['换脸', 'face swap', 'faceswap', 'reactor'],
 '角色一致性 / Character consistency': ['角色一致', '人物一致', '同一个人物', 'character consistency', 'consistent character', 'instantid', 'pulid', 'ipadapter', 'ip adapter'],
 '人脸 / Face': ['人脸', 'face', 'facial'],
 '姿态 / Pose': ['姿态', '骨骼', 'pose', 'openpose'],
 '深度 / Depth': ['深度', 'depth'],
 '线稿 / Line art': ['线稿', '边缘检测', 'lineart', 'line art', 'canny'],
 '音频 / Audio': ['音频', 'audio', '声音'],
 '语音合成 / Text to speech': ['配音', '文字转语音', '语音合成', 'text to speech', 'tts', 'voice cloning'],
 '音乐 / Music': ['音乐', '配乐', 'music'],
 '字幕 / Subtitles': ['字幕', '语音识别', '转录', 'subtitle', 'subtitles', 'whisper', 'transcription'],
 '口型 / Lip sync': ['口型', '嘴型', '对口型', 'lip sync', 'lipsync', 'talking head'],
 '提示词 / Prompt': ['提示词', 'prompt', 'prompting'],
 '图片反推 / Caption': ['反推', '图片描述', '看图写', 'caption', 'captioning', 'tagger', 'interrogate'],
 '批量处理 / Batch': ['批量', 'batch', '批处理'],
 '3D / 3D': ['三维', '3d', 'mesh', '建模'],
 '训练 / Training': ['训练', 'training', 'finetune', 'fine tuning'],
 '模型合并 / Model merge': ['模型合并', '融合模型', 'model merge', 'checkpoint merge'],
 '图像拼接 / Image stitching': ['拼图', '拼接', 'image stitching', 'image grid', 'collage'],
 '色彩 / Color': ['调色', '颜色', 'color grading', 'color correction', 'color transfer'],
 '逻辑 / Logic': ['逻辑', '运算', '布尔', '条件判断', '逻辑运算', '数学运算', 'logic', 'boolean', 'math', 'arithmetic', 'compare', 'comparison', 'conditional'],
}

TAG_GROUPS = {
 '图像 / Image': list(CONCEPTS)[2:3] + list(CONCEPTS)[7:18] + list(CONCEPTS)[29:31],
 '视频 / Video': list(CONCEPTS)[:2] + list(CONCEPTS)[3:7] + [list(CONCEPTS)[22]],
 '音频 / Audio': list(CONCEPTS)[18:22],
 '语言与理解 / Language': list(CONCEPTS)[23:25],
 '模型与生成 / Generation': list(CONCEPTS)[27:29],
 '工作流 / Workflow': [list(CONCEPTS)[25], list(CONCEPTS)[31]],
 '三维 / 3D': [list(CONCEPTS)[26]],
 '性能 / Performance': [],
 '文件与资产 / Assets': [],
}

EXTRA_CONCEPTS = {
 '图像 / Image': {
  '重打光 / Relighting': ['重打光','重新打光','relighting','relight','ic light'],
  '图层合成 / Layers': ['图层','layer compositing','layer composition','blend modes'],
  'PSD 导入导出 / PSD': ['psd','photoshop document'],
  '裁剪与尺寸 / Crop and resize': ['裁剪','调整尺寸','crop','resize'],
  '风格迁移 / Style transfer': ['风格迁移','style transfer'],
  '目标检测 / Object detection': ['目标检测','object detection','grounding dino','yolo'],
 },
 '视频 / Video': {
  '目标跟踪 / Tracking': ['目标跟踪','object tracking','video tracking'],
  '光流 / Optical flow': ['光流','optical flow'],
  '视频剪辑 / Video editing': ['视频剪辑','video editing','video trimming'],
  '镜头切分 / Shot detection': ['镜头切分','shot detection','scene detection'],
  '摄像机运动 / Camera motion': ['运镜','摄像机运动','camera motion','camera control'],
  '视频转视频 / Video to video': ['视频转视频','video to video','v2v'],
 },
 '音频 / Audio': {
  '人声分离 / Audio separation': ['人声分离','伴奏提取','audio separation','stem separation','demucs'],
  '音频降噪 / Audio denoising': ['音频降噪','语音降噪','audio denoising','speech enhancement'],
  '声音克隆 / Voice cloning': ['声音克隆','voice cloning','clone voice'],
  '变声 / Voice conversion': ['变声','voice conversion'],
  '音效生成 / Sound effects': ['音效生成','sound effects','foley'],
  '音频剪辑 / Audio editing': ['音频剪辑','audio editing','audio crop','audio trim'],
 },
 '语言与理解 / Language': {
  '文字识别 / OCR': ['文字识别','ocr','text recognition'],
  '本地大语言模型 / Local LLM': ['本地大语言模型','本地语言模型','ollama','llama cpp','local llm'],
  '视觉问答 / Visual QA': ['视觉问答','看图问答','visual question answering','vqa'],
  '翻译 / Translation': ['翻译','translation','translate'],
  '远程 API / Remote API': ['远程api','remote api','api integration','openai api'],
 },
 '模型与生成 / Generation': {
  '采样器 / Samplers': ['采样器','sampler','sampling'],
  '调度器 / Schedulers': ['调度器','scheduler'],
  'ControlNet / ControlNet': ['控制网','controlnet'],
  '区域控制 / Regional control': ['区域控制','regional prompting','regional conditioning'],
  'LoRA 管理 / LoRA management': ['lora管理','lora manager','lora loader','lora stack'],
  '潜空间处理 / Latent tools': ['潜空间','latent processing','latent blend','latent noise'],
 },
 '工作流 / Workflow': {
  '插件管理 / Plugin management': ['插件管理','plugin manager','custom node manager','comfyui manager'],
  '模型管理 / Model management': ['模型管理','model manager','model management','model browser'],
  '模型下载 / Model download': ['模型下载','model downloader','model download','download models'],
  '工作流管理 / Workflow management': ['工作流管理','workflow manager','workflow management'],
  '队列管理 / Queue management': ['队列管理','queue manager','queue management'],
  '界面增强 / UI tools': ['界面增强','ui enhancement','user interface','frontend extension'],
  '运行监控 / Monitoring': ['运行监控','资源监控','system monitor','resource monitor'],
 },
 '性能 / Performance': {
  '显存优化 / VRAM optimization': ['显存优化','显存不够','爆显存','low vram','lowvram','memory optimization','offload'],
  '推理加速 / Acceleration': ['推理加速','跑得慢','inference acceleration','sageattention','torch compile'],
  '缓存加速 / Cache acceleration': ['缓存加速','teacache','tea cache','easycache','easy cache','first block cache'],
  '量化加载 / Quantized loading': ['量化','quantization','quantized','gguf'],
  '分块处理 / Tiled processing': ['分块处理','tiled diffusion','tiled vae','tiled sampling'],
  '多显卡 / Multi GPU': ['多显卡','multi gpu','multigpu'],
 },
 '文件与资产 / Assets': {
  '元数据 / Metadata': ['元数据','metadata','exif','png info'],
  '格式转换 / Format conversion': ['格式转换','format conversion','format converter'],
  '图片浏览 / Image browser': ['图片浏览','image browser','image gallery'],
  '文件管理 / File management': ['文件管理','file manager','file management'],
  '保存与导出 / Save and export': ['保存导出','save image','save video','export image'],
 },
 '三维 / 3D': {
  '三维重建 / 3D reconstruction': ['三维重建','3d reconstruction','image to 3d'],
  '网格处理 / Mesh processing': ['网格处理','mesh processing','mesh simplification'],
  '材质纹理 / Textures': ['材质','纹理生成','texture generation','texture baking'],
 },
}
for group, concepts in EXTRA_CONCEPTS.items():
    CONCEPTS.update(concepts)
    TAG_GROUPS[group].extend(concepts)

# Search-only Chinese → English. These do not become category chips.
GLOSSARY = {
    '逻辑运算': ['logic', 'boolean', 'math', 'compare'],
    '条件判断': ['conditional', 'boolean', 'compare'],
    '数学运算': ['math', 'arithmetic', 'number'],
    '随机种子': ['seed', 'random'],
    '逻辑': ['logic', 'boolean', 'math', 'compare', 'conditional'],
    '运算': ['math', 'arithmetic', 'calculate'],
    '布尔': ['boolean', 'logic'],
    '数学': ['math', 'arithmetic'],
    '开关': ['boolean', 'logic', 'toggle'],
    '比较': ['compare', 'comparison'],
    '判断': ['conditional', 'boolean'],
    '条件': ['conditional', 'boolean'],
    '随机': ['random', 'seed'],
    '种子': ['seed'],
    '裁剪': ['crop'],
    '旋转': ['rotate', 'rotation'],
    '翻转': ['flip', 'mirror'],
    '缩放': ['resize', 'scale'],
    '尺寸': ['resize', 'width', 'height'],
    '加载': ['load', 'loader'],
    '读取': ['load', 'loader', 'read'],
    '保存': ['save', 'writer'],
    '写入': ['save', 'writer'],
    '字符串': ['string', 'concat', 'text'],
    '文本': ['string', 'concat', 'text'],
    '循环': ['loop', 'iterate'],
    '延迟': ['delay', 'sleep', 'wait'],
    '路径': ['path', 'directory', 'folder'],
    '文件': ['file', 'path'],
    '模糊': ['blur', 'gaussian'],
    '锐化': ['sharpen'],
    '潜空间': ['latent'],
    '采样': ['sampler', 'sampling'],
    '调度': ['scheduler'],
    '预览': ['preview'],
    '翻译': ['translate', 'translation'],
    '检测': ['detect', 'detection'],
    '识别': ['recognition', 'detect'],
    '风格': ['style'],
    '水印': ['watermark'],
    '二维码': ['qrcode', 'qr'],
    '嵌入': ['embedding'],
    '控制网': ['controlnet'],
    '工作流': ['workflow'],
    '队列': ['queue'],
    '浏览器': ['browser'],
}


def normalize(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(text))
    return re.sub(r'[\W_]+', ' ', text.casefold()).strip()


def contains(text, term):
    term = normalize(term)
    if re.search(r'[\u4e00-\u9fff]', term):
        return term in text
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))


def evidence(item):
    raw = ' '.join(str(item.get(k, '')) for k in ('title', 'description', 'preemptions'))
    raw = re.sub(r'https?://\S+|`[^`]*`', ' ', raw)
    raw = re.sub(r'\bhugging\s*face\b', 'huggingface', raw, flags=re.I)
    # Protect brand names before camel-case splitting and ignore installation prose.
    raw = re.sub(r'(?im)^.*(?:pip install|git clone|install via|install using).*$',' ', raw)
    text = normalize(raw)
    return {tag: [word for word in words if contains(text, word)] for tag, words in CONCEPTS.items() if any(contains(text, word) for word in words)}


STOP = {
    'i', 'want', 'to', 'a', 'an', 'the', 'for', 'with', 'and', 'can', 'please', 'plugin', 'plugins',
    'help', 'me', 'that', 'node', 'nodes', 'comfy', 'comfyui', 'custom',
    '插件', '节点', '帮我', '一个', '我想', '可以', '怎么', '如何', '实现', '功能', '相关', '找到', '找', '做',
}


def add_token(tokens, word):
    word = normalize(word)
    if word and word not in STOP and len(word) > 1 and word not in tokens:
        tokens.append(word)


def _clusters():
    groups = []
    for words in (*CONCEPTS.values(), *([key, *vals] for key, vals in GLOSSARY.items())):
        groups.append({normalize(word) for word in words if normalize(word)})
    merged = True
    while merged:
        merged = False
        nxt = []
        for group in groups:
            for acc in nxt:
                if group & acc:
                    acc |= group
                    merged = True
                    break
            else:
                nxt.append(set(group))
        groups = nxt
    index = {}
    for group in groups:
        frozen = tuple(sorted(group))
        for word in group:
            index[word] = frozen
    return index


INDEX = _clusters()


def within_one(a, b):
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or min(la, lb) < 2:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = diffs = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        diffs += 1
        if diffs > 1:
            return False
        if la == lb:
            i += 1
        j += 1
    return True


def related_terms(text):
    pieces = re.findall(r'[a-z0-9]+', text) + re.findall(r'[\u4e00-\u9fff]+', text)
    found = []
    for piece in pieces:
        if len(piece) < 2 or piece in STOP:
            continue
        cjk = bool(re.search(r'[\u4e00-\u9fff]', piece))
        for key, cluster in INDEX.items():
            if ' ' in key:
                hit = contains(text, key)
            elif cjk != bool(re.search(r'[\u4e00-\u9fff]', key)):
                hit = False
            elif piece == key:
                hit = True
            elif cjk and (piece in key or key in piece):
                hit = True
            elif not cjk and min(len(piece), len(key)) >= 3 and (key.startswith(piece) or piece.startswith(key)):
                hit = True
            else:
                hit = within_one(piece, key)
            if hit:
                found.extend(cluster)
    return found


def interpret(query):
    text = normalize(query)
    intents = [tag for tag, words in CONCEPTS.items() if any(contains(text, word) for word in words)]
    patterns = {
        '抠图 / Background removal': r'(去掉|去除|删除|移除).{0,12}背景|背景.{0,8}(去掉|去除|删除|移除)',
        '图生视频 / Image to video': r'(图片|照片|图像).{0,12}(动起来|变成动画|生成视频|变成视频)',
        '文生视频 / Text to video': r'(文字|文本|描述).{0,12}(生成|制作).{0,6}视频',
        '语音合成 / Text to speech': r'(文字|文本).{0,12}(读出来|朗读|说出来|语音)',
    }
    for tag, pattern in patterns.items():
        if re.search(pattern, text) and tag not in intents:
            intents.append(tag)
    tokens = []
    for word in re.findall(r'[a-z0-9]+', text):
        add_token(tokens, word)
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        add_token(tokens, segment)
    for word in related_terms(text):
        add_token(tokens, word)
    token_set = set(tokens)
    for tag, words in CONCEPTS.items():
        if tag not in intents and token_set & {normalize(word) for word in words}:
            intents.append(tag)
    for tag in intents:
        for word in CONCEPTS[tag]:
            add_token(tokens, word)
    return text, intents, tokens


def term_hits(haystack, term):
    if contains(haystack, term):
        return True
    if re.search(r'[\u4e00-\u9fff]', term) or len(term) < 4:
        return False
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(term), haystack))


def ngram_score(query, searchable, tokens):
    def grams(value):
        value = normalize(value).replace(' ', '')
        if len(value) < 2:
            return {value} if value else set()
        return {value[i:i + 2] for i in range(len(value) - 1)}
    needle = grams(query)
    for token in tokens:
        needle |= grams(token)
    hay = grams(searchable[:500])
    if not needle or not hay:
        return 0
    return len(needle & hay) / len(needle)


def scored_row(item, matched, terms, title_hits, text, title, intents):
    proof = item.get('evidence', {})
    coverage = len(matched) / len(intents) if intents else 0
    score = coverage * 100 + len(matched) * 10 + len(terms) * 2 + len(title_hits) * 12 + (25 if text and text in title else 0)
    evidence_terms = list(dict.fromkeys(word for tag in matched for word in proof.get(tag, ())))[:8] or terms[:8]
    return {**{k: v for k, v in item.items() if k not in ('evidence', 'search_text')}, 'score': score,
            'matched_tags': matched, 'missing_tags': [tag for tag in intents if tag not in matched],
            'matched_terms': terms, 'evidence_terms': evidence_terms}


def rank(items, query, extra_terms=None):
    text, intents, tokens = interpret(query)
    for term in extra_terms or []:
        _, inferred, words = interpret(term)
        intents.extend(tag for tag in inferred if tag not in intents)
        for word in words:
            add_token(tokens, word)
    results = []
    for item in items:
        proof = item.get('evidence', {})
        matched = [tag for tag in intents if tag in proof]
        title = normalize(item['title'])
        searchable = item.get('search_text', normalize(item['title'] + ' ' + item['description']))
        terms = [token for token in tokens if term_hits(searchable, token)]
        title_hits = [token for token in tokens if term_hits(title, token)]
        exact = bool(text and text in searchable)
        if text and not matched and not terms and not exact:
            continue
        results.append(scored_row(item, matched, terms, title_hits, text, title, intents))
    if text and not results:
        related = []
        for item in items:
            searchable = item.get('search_text', normalize(item['title'] + ' ' + item['description']))
            sim = ngram_score(text, searchable, tokens)
            if sim < 0.22:
                continue
            row = scored_row(item, [], [], [], text, normalize(item['title']), intents)
            row['score'] = sim * 40
            related.append(row)
        related.sort(key=lambda row: (-row['score'], row['title'].casefold()))
        return related[:40], intents, True
    results.sort(key=lambda row: (-row['score'], row['title'].casefold()))
    return results, intents, False
