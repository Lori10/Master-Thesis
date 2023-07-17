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
from sklearn.metrics.pairwise import cosine_similarity
from scipy import stats
import pickle
from sklearn.metrics import pairwise_distances


def parse_arguments():
    parser = argparse.ArgumentParser(description="Auto-Active-CoT-Combination-KMeans")
    parser.add_argument(
        "--dataset", type=str, default="gsm8k",
        choices=["aqua", "gsm8k"], help="dataset used for experiment"
    )

    parser.add_argument(
        "--data_path", type=str, default="../datasets/gsm8k/train.jsonl",
        choices=["../datasets/gsm8k/train.jsonl", "../datasets/AQuA/train.json"], help="dataset used for experiment"
    )

    parser.add_argument(
        "--model", type=str, default="gpt-3.5-turbo", choices=["gpt-3.5-turbo"], help="model used for decoding."
    )
    # parser.add_argument(
    #     "--max_ra_len", type=int, default=5, help="maximum number of reasoning chains"
    # )
    parser.add_argument("--random_seed", type=int, default=42, help="random seed")
    parser.add_argument(
        "--num_trails", type=int, default=5, help="number of trails to run for each question"
    )
    parser.add_argument(
        "--sampling", type=str, default="center", help="whether to sample the cluster center first"
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
        "--distance_metric", type=str, default='cosine', choices=['cosine', 'euclidean'], help="sort the final result by given option"
    )
    # parser.add_argument(
    #     "--max_length_cot", type=int, default=256, help="maximum length of output tokens by model for reasoning extraction"
    # )
    # parser.add_argument(
    #     "--api_time_interval", type=float, default=1.0, help="how many seconds sleep between each request"
    # )
    parser.add_argument(
        "--temperature", type=float, default=0.7, help="temperature for llm decoding"
    )
    parser.add_argument(
        "--dir_prompts", type=str, default="prompts_active", help="prompts to use"
    )

    parser.add_argument(
        "--uncertainty_scores_dir", type=str, default='uncertainty_scores/', help='directory where the uncertainty scores are saved'
    )

    parser.add_argument(
        "--nr_demos", type=int, default=5, help='number of demonstrations'
    )
    parser.add_argument(
        "--answers_are_available", type=bool, default=True, help='true if answers are available in the test dataset, false otherwise'
    )

    args = parser.parse_args()

    if args.dataset == "gsm8k":
        args.direct_answer_trigger = "\nTherefore, the answer (arabic numerals) is"

    elif args.dataset == "aqua":
        args.direct_answer_trigger = "\nThe answer is"
    else:
        raise NotImplementedError

    if args.answers_are_available:
        args.demos_save_dir = "labeled_demos/"
    else:
        args.demos_save_dir = "unlabeled_demos/"

    # "Therefore, the answer ..." -> "The answer ..."
    trigger = args.direct_answer_trigger.replace("\nTherefore, ", "")
    args.direct_answer_trigger_for_zeroshot = trigger[0].upper() + trigger[1:]
    args.direct_answer_trigger_for_zeroshot_cot = args.direct_answer_trigger
    args.direct_answer_trigger_for_fewshot = "The answer is"
    args.cot_trigger = "Let's think step by step."
    return args

def f1_score(distances, uncertainties, beta=1.5):
    # distances is the precision, uncertainties is the recall, beta is the weight of recall
    normalized_distances = softmax(distances)
    normalized_uncertainties = softmax(uncertainties)
    f1_scores = ((beta**2 + 1) * normalized_distances * normalized_uncertainties) / (beta**2 * normalized_distances + normalized_uncertainties)
    return f1_scores

def square_prob(scores):
        return (scores ** 2)/ sum(scores ** 2)
    
def softmax(scores):
    return np.exp(scores) / np.sum(np.exp(scores), axis=0)

def generate_doc_embedding(corpus, encoder=OpenAIEmbeddings()):
    return np.array(encoder.embed_documents(corpus))


def compute_distances(args, embeddings, selected_data, uncertainty_list, question_idxs):
    if args.distance_metric == 'cosine':
        if len(selected_data) == 1:
            D2 = 1 - pairwise_distances(embeddings, selected_data, metric='cosine', n_jobs=-1).ravel().astype(float)
        else:
            newD = 1 - pairwise_distances(embeddings, [selected_data[-1]], metric='cosine', n_jobs=-1).ravel().astype(float)
            for i in range(len(embeddings)):
                if D2[i] < newD[i]:
                    D2[i] = newD[i]

        D2[D2 > 0.999] = 1
        not_selected_questions_distances_uncertainties = [(question_idx, distance, uncertainty) for question_idx, distance, uncertainty in zip(question_idxs, D2, uncertainty_list) if distance != 1]
    
    elif args.distance_metric == 'eucledian':
        if len(selected_data) == 1:
            D2 = pairwise_distances(embeddings, selected_data, metric='euclidean', n_jobs=-1).ravel().astype(float)
        else:
            newD = pairwise_distances(embeddings, [selected_data[-1]], metric='euclidean', n_jobs=-1).ravel().astype(float)
            for i in range(len(embeddings)):
                if D2[i] > newD[i]:
                    D2[i] = newD[i] 

        D2[D2 < 0.0001] = 0
        not_selected_questions_distances_uncertainties = [(question_idx, distance, uncertainty) for question_idx, distance, uncertainty in zip(question_idxs, D2, uncertainty_list) if distance != 0]

    return not_selected_questions_distances_uncertainties


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

    args.demos_save_dir = f"{args.demos_save_dir}auto_active_cot_kmeans_plusplus/{args.dataset}/"

    if not os.path.exists(args.uncertainty_scores_dir):
        os.makedirs(args.uncertainty_scores_dir)
    uncertainty_filepath = f"{args.uncertainty_scores_dir}AutoActiveKMeansPlusPlus_{args.dataset}_numtrials_{args.num_trails}_sortby_{args.sort_by}"


    set_random_seed(args.random_seed)
    dataloader = create_dataloader(args)
    if args.dataset_size_limit <= 0:
        args.dataset_size_limit = len(dataloader)
    else:
        dataloader = dataloader[:args.dataset_size_limit] # replace 7 with 1000; only take 1000 questions randomly to annotate, randomness decided by seed
    print(f"Proceeding with data size: {len(dataloader)}")

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
    j = 0
    demos = []

    while j < args.nr_demos:
        if len(selected_data) == 1:
            if args.distance_metric == 'cosine':
                D2 = 1 - pairwise_distances(embeddings, selected_data, metric='cosine', n_jobs=-1).ravel().astype(float)
            elif args.distance_metric == 'euclidean':
                D2 = pairwise_distances(embeddings, selected_data, metric='euclidean', n_jobs=-1).ravel().astype(float)
            else:
                raise NotImplementedError
        else:
            if args.distance_metric  == 'cosine':
                newD = 1 - pairwise_distances(embeddings, [selected_data[-1]], metric='cosine', n_jobs=-1).ravel().astype(float)
                for i in range(len(embeddings)):
                    if D2[i] < newD[i]:
                        D2[i] = newD[i]
            elif args.distance_metric == 'euclidean':
                newD = pairwise_distances(embeddings, [selected_data[-1]], metric='euclidean', n_jobs=-1).ravel().astype(float)
                for i in range(len(embeddings)):
                    if D2[i] > newD[i]:
                        D2[i] = newD[i] 
            else:
                raise NotImplementedError

        if args.distance_metric == 'cosine':
            D2[D2 > 0.999] = 1
            not_selected_questions_distances_uncertainties = [(question_idx, distance, uncertainty) for question_idx, distance, uncertainty in zip(questions_idxs, D2, uncertainty_list) if distance != 1]

        elif args.distance_metric == 'euclidean':
            D2[D2 < 0.0001] = 0
            not_selected_questions_distances_uncertainties = [(question_idx, distance, uncertainty) for question_idx, distance, uncertainty in zip(questions_idxs, D2, uncertainty_list) if distance != 0]

        not_selected_questions_idxs = [question_idx for question_idx, _, _ in not_selected_questions_distances_uncertainties]
        not_selected_distances = [distance for _, distance, _ in not_selected_questions_distances_uncertainties]
        not_selected_uncertainties = [uncertainty for _, _, uncertainty in not_selected_questions_distances_uncertainties]
        not_selected_f1_scores = f1_score(not_selected_distances, not_selected_uncertainties)
        probs = softmax(not_selected_f1_scores)

        customDist = stats.rv_discrete(name='custm', values=(not_selected_questions_idxs, probs))
        selected_idx = customDist.rvs(size=1)[0]
        selected_idxs.append(selected_idx)
        selected_data.append(embeddings[selected_idx])
        demos.append(dataloader[selected_idx])
        j += 1

        print('Iteration: ', j)
        print('Length of not selected questions:', len(not_selected_questions_distances_uncertainties))
        print('Selected question idx: ', selected_idx)
        if args.distance_metric == 'cosine':
            print("Number of distances equal to 1: ", len(D2[D2 == 1]))
        else:
            print("Number of distances equal to 0: ", len(D2[D2 == 0]))
        print('*' * 50)

    demos = {"demo": demos}
    with open(args.demos_save_dir + 'demos', 'w', encoding="utf-8") as write_f:
        json.dump(demos, write_f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    main()
