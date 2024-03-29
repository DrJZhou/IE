# coding=utf-8
import json

import torch
import logging
import numpy as np
from tqdm import tqdm
from sklearn.metrics import f1_score
import requests
from functions_utils import tensor_to_list, list_to_tensor, tensor_to_array
from io import BytesIO
import gzip
import pickle
logger = logging.getLogger(__name__)


def zip_pickle_compress(data):
    buf = BytesIO()
    with gzip.GzipFile(mode='wb', fileobj=buf) as fp:
        gzip_value = pickle.dumps(data)
        fp.write(gzip_value)
    return buf


def unzip_pickle_compress(data):
    buf = BytesIO(data)
    gf = gzip.GzipFile(fileobj=buf)
    content = pickle.loads(gf.read())
    return content


def get_base_out(loader):
    headers = {
        'content-encoding': 'gzip',
        'accept-encoding': 'gzip',
        'Content-type': 'application/json;charset=UTF-8', 'Accept': 'text/plain'}
    with torch.no_grad():
        for idx, _batch in enumerate(tqdm(loader, desc=f'Get predict logits')):
            # for key in _batch.keys():
            #     _batch[key] = _batch[key].to(device)
            json_data = {"userID": 1, "batch_data": tensor_to_array(_batch)}
            content_data = requests.post(url='http://localhost:5001/client_forward', data=zip_pickle_compress(json_data).getvalue(), headers=headers).content
            json_data = unzip_pickle_compress(content_data)
            content_data = requests.post(url='http://localhost:5000/service_forward', data=zip_pickle_compress(json_data).getvalue(), headers=headers).content
            json_data = unzip_pickle_compress(content_data)
            tmp_out = json_data['batch_data']["output"]
            yield tmp_out, _batch['labels']


# def pointer_decode(logits, text, type2id):
#     id2type = {type2id[type_]: type_ for type_ in type2id.keys()}
#     ans_tmp = np.argmax(logits, -1)
#     BIO_Label = [id2type[i] for i in ans_tmp]
#     entity_text = ""
#     entity_type = None
#     entity_start = 0
#     entities = []
#     for i in range(len(BIO_Label)):
#         if BIO_Label[i].startswith("B"):
#             entity_start = i
#             entity_text = text[i]
#             entity_type = BIO_Label[i].split("-")[1]
#         elif BIO_Label[i].startswith("I"):
#             entity_text += text[i]
#         else:
#             if len(entity_text):
#                 entities.append([entity_text, entity_type, entity_start])
#             entity_text = ""
#     return entities


def pointer_decode(logits, text, type2id):
    id2type = {type2id[type_]: type_ for type_ in type2id.keys()}
    ans_tmp = np.argmax(logits, -1)
    BIO_Label = [id2type[i] for i in ans_tmp]
    entity_text = ""
    entity_type = None
    entity_start = 0
    entities = []
    for i in range(len(BIO_Label)):
        if BIO_Label[i].startswith("B"):
            entity_start = i
            entity_text = text[i]
            entity_type = BIO_Label[i].split("-")[1]
        elif BIO_Label[i].startswith("M"):
            entity_text += text[i]
        elif BIO_Label[i].startswith("E"):
            entity_text += text[i]
            if len(entity_text):
                entities.append([entity_text, entity_type, entity_start])
            entity_text = ""
    return entities


def calculate_metric(gt, predict):
    """
    计算 tp fp fn
    """
    tp, fp, fn = 0, 0, 0
    for entity_predict in predict:
        flag = 0
        for entity_gt in gt:
            if entity_predict[0] == entity_gt[0] and entity_predict[1] == entity_gt[1] and entity_predict[2] == \
                    entity_gt[2]:
                flag = 1
                tp += 1
                break
        if flag == 0:
            fp += 1

    fn = len(gt) - tp

    return np.array([tp, fp, fn])


def get_p_r_f(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp != 0 else 0
    r = tp / (tp + fn) if tp + fn != 0 else 0
    f1 = 2 * p * r / (p + r) if p + r != 0 else 0
    return np.array([p, r, f1])


def evaluation(dev_info):
    """
    线下评估 trigger 模型
    """
    dev_loader, dev_callback_info = dev_info

    pred_logits = None
    labels = None
    for tmp_pred, batch_label in get_base_out(dev_loader):
        tmp_pred = np.array(tmp_pred)
        batch_label = np.array(batch_label)
        # print(tmp_pred.shape[:], batch_label.shape[:])
        if pred_logits is None:
            pred_logits = tmp_pred
            labels = batch_label
        else:
            pred_logits = np.append(pred_logits, tmp_pred, axis=0)
            labels = np.append(labels, batch_label, axis=0)

    assert len(pred_logits) == len(dev_callback_info)
    assert len(labels) == len(dev_callback_info)

    zero_pred = 0
    with open("type2id.json", "r", encoding='utf-8') as f:
        type2id = json.load(f)
    tp, fp, fn = 0, 0, 0

    labels_pred = []
    labels_truth = []
    for tmp_pred, tmp_label, tmp_callback in zip(pred_logits, labels, dev_callback_info):
        text, gt_triggers = tmp_callback
        tmp_pred = tmp_pred[1:1 + len(text)]
        labels_pred += list(np.argmax(tmp_pred, axis=-1))
        labels_truth += list(tmp_label[1:1 + len(text)])
        assert len(labels_pred) == len(labels_truth)
        pred_triggers = pointer_decode(tmp_pred, text, type2id)

        if not len(pred_triggers):
            zero_pred += 1

        tmp_tp, tmp_fp, tmp_fn = calculate_metric(gt_triggers, pred_triggers)

        tp += tmp_tp
        fp += tmp_fp
        fn += tmp_fn

    p, r, f1 = get_p_r_f(tp, fp, fn)

    bio_f1_score = f1_score(labels_truth, labels_pred, average="macro")
    metric_str = f'[MIRCO] precision: {p:.4f}, recall: {r:.4f}, f1: {f1:.4f}, bio_f1: {bio_f1_score:.4f}\n'
    metric_str += f'Zero pred nums: {zero_pred}'
    return metric_str, f1, bio_f1_score
