import random
# from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
import numpy as np
import json
import matplotlib.pyplot as plt
import argparse
from utils import fix_seed
from langchain.embeddings import OpenAIEmbeddings
import os
from utils import *
from generate_demo_active import generate_uncertainty_qes
import pandas as pd
from sklearn.metrics.pairwise import euclidean_distances
from scipy import stats
import pickle
from sklearn.metrics import pairwise_distances

def parse_arguments():
    parser = argparse.ArgumentParser(description="Auto-Active-CoT-Combination-KMeans")
    parser.add_argument(
        "--dataset", type=str, default="gsm8k",
        choices=["aqua", "gsm8k", "commonsensqa", "addsub", "multiarith", "strategyqa", "svamp", "singleeq", "coin_flip", "last_letters"], help="dataset used for experiment"
    )
    parser.add_argument(
        "--model", type=str, default="text-davinci-002", choices=["text-davinci-002", "code-davinci-002"], help="model used for decoding."
    )
    parser.add_argument(
        "--max_ra_len", type=int, default=5, help="maximum number of reasoning chains"
    )
    parser.add_argument("--random_seed", type=int, default=42, help="random seed")
    parser.add_argument(
        "--num_trails", type=int, default=5, help="number of trails to run for each question"
    )
    parser.add_argument(
        "--sampling", type=str, default="center", help="whether to sample the cluster center first"
    )
    parser.add_argument(
        "--demos_save_dir", type=str, default="demos/", help="directory to save the generated demos"
    )

    parser.add_argument(
        "--method", type=str, default="few_shot_cot", choices=["zero_shot_cot", "few_shot_cot"], help="method"
    )
    parser.add_argument(
        "--dataset_size_limit", type=int, default=10, help="limit the size of training data used to select the demonstrations"
    )
    parser.add_argument(
        "--sort_by", type=str, default='disagreement', choices=['disagreement', 'variance', 'entropy'], help="sort the final result by given option"
    )
    parser.add_argument(
        "--max_length_cot", type=int, default=256, help="maximum length of output tokens by model for reasoning extraction"
    )
    parser.add_argument(
        "--api_time_interval", type=float, default=1.0, help="how many seconds sleep between each request"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.7, help=""
    )
    parser.add_argument(
        "--dir_prompts", type=str, default="prompts_active", help="prompts to use"
    )
    parser.add_argument(
        "--concat_length", type=int, default=2, help='Used for task last_letters, indicates length of last letter concat'
    )
    parser.add_argument(
        "--nr_demos", type=int, default=7, help='number of demonstrations'
    )

    args = parser.parse_args()

    if args.dataset == "gsm8k":
        args.data_path = "../datasets/gsm8k/train.jsonl"
        args.direct_answer_trigger = "\nTherefore, the answer (arabic numerals) is"

    elif args.dataset == "aqua":
        args.data_path = "../datasets/AQuA/train.json" 
        args.direct_answer_trigger = "\nThe answer is"

    # "Therefore, the answer ..." -> "The answer ..."
    trigger = args.direct_answer_trigger.replace("\nTherefore, ", "")
    args.direct_answer_trigger_for_zeroshot = trigger[0].upper() + trigger[1:]
    args.direct_answer_trigger_for_zeroshot_cot = args.direct_answer_trigger
    args.direct_answer_trigger_for_fewshot = "The answer is"
    args.cot_trigger = "Let's think step by step."
    return args

def f1_score(question_idxs, distances, uncertainties, beta=1.5):
    normalized_distances = distances / sum(distances)
    normalized_uncertainties = uncertainties / sum(uncertainties)
    f1_metric = ((beta**2 + 1) * normalized_distances * normalized_uncertainties) / (beta**2 * normalized_distances + normalized_uncertainties)
    return question_idxs, f1_metric

def main():
    args = parse_arguments()
    if not os.path.exists(args.demos_save_dir):
        os.makedirs(args.demos_save_dir)
        os.makedirs(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus')
        os.makedirs(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus/' + args.dataset)
    elif not os.path.exists(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus'):
        os.makedirs(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus')
        os.makedirs(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus/' + args.dataset)
    elif not os.path.exists(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus/' + args.dataset):
        os.makedirs(args.demos_save_dir + 'auto_active_cot_kmeans_plusplus/' + args.dataset)

    uncertainty_estimation_dir = f"{args.demos_save_dir}auto_active_cot_kmeans_plusplus/uncertainty_estimation/"
    if not os.path.exists(uncertainty_estimation_dir):
        os.makedirs(uncertainty_estimation_dir)

    args.demos_save_dir = f"{args.demos_save_dir}auto_active_cot_kmeans_plusplus/{args.dataset}/"
    max_ra_len = args.max_ra_len
    num_clusters = args.nr_demos

    set_random_seed(args.random_seed)
    dataloader = create_dataloader(args)
    if args.dataset_size_limit <= 0:
        args.dataset_size_limit = len(dataloader)
    else:
        dataloader = dataloader[:args.dataset_size_limit] # replace 7 with 1000; only take 1000 questions randomly to annotate, randomness decided by seed
    print(f"Dataloader size: {len(dataloader)}")

    #uncertainty_list = []
    corpus = []
    questions_idxs = []
    for idx, example in enumerate(dataloader):
        #print(f'Question: {example["question"]}\n')
        #uncertainty_record = generate_uncertainty_qes(args, example)
        corpus.append(example['question'])
        #uncertainty_list.append(uncertainty_record['entropy'])
        questions_idxs.append(idx)

    # with open('uncertainties.pkl', 'wb') as f:
    #     pickle.dump(uncertainty_list, f)
    

    #encoder = OpenAIEmbeddings()
    #embeddings = np.array(encoder.embed_documents(corpus))
    
    file = open("embeddings", "rb")
    embeddings = np.load(file)

    with open('uncertainties.pkl', 'rb') as f:
        uncertainty_list = pickle.load(f)

    uncertainties_series = pd.Series(data=uncertainty_list, index=questions_idxs)
    first_question_idx = list(uncertainties_series.sort_values(ascending=False).head(1).index)[0]
    selected_idxs = [first_question_idx]
    selected_data = [embeddings[first_question_idx]]
    print(first_question_idx)
    j = 0
    while True:
        if j == 7:
            break 

        if len(selected_data) == 1:
            D2 = pairwise_distances(embeddings, selected_data, metric='cosine').ravel().astype(float)
        
        else:
            newD = pairwise_distances(embeddings, [selected_data[-1]], metric='cosine').ravel().astype(float)
            for i in range(len(embeddings)):
                if D2[i] > newD[i]:
                    D2[i] = newD[i]

        D2 = D2.ravel().astype(float)  
        dist_prob = (D2 ** 2)/ sum(D2 ** 2)
        customDist = stats.rv_discrete(name='custm', values=(questions_idxs, dist_prob))

        #np.random.seed(10)
        selected_idx = customDist.rvs(size=1)[0]
        selected_idxs.append(selected_idx)
        selected_data.append(embeddings[selected_idx])
        j += 1

        # print the number of distances in D2 which are equal to 0
        print('Iteration: ', j)
        print("Number of distances equal to 0: ", len(D2[D2 == 0]))

    
if __name__ == "__main__":
    main()
