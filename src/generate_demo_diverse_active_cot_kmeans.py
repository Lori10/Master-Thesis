from sklearn.cluster import KMeans
import json
import argparse
import os
from constant_vars import *
import datetime
import pickle
import time
from utils.load_data import create_dataloader
from utils.prompts_llm import create_prompts_inference, initialize_llmchain, initialize_llm
from utils.uncertainty_estimation import generate_uncertainty_all_questions, sort_uncertainty
from utils.embedding_generation import generate_corpus_embeddings
from utils.filter_simple_examples import filter_examples_with_labels, filter_examples_no_labels

def parse_arguments():
    parser = argparse.ArgumentParser(description="Auto-Active-CoT-KMeans")
    parser.add_argument(
        "--dataset", type=str, default="gsm8k",
        choices=["aqua", "gsm8k"], help="dataset used for experiment"
    )

    parser.add_argument(
        "--data_path", type=str, default="../datasets/gpt35_zeroshotcot_training_data/gsm8k/QA_record_prompt1.txt",
        choices=["../datasets/original/gsm8k/train.jsonl", "../datasets/original/AQuA/train.json",
                 "../datasets/gpt35_zeroshotcot_training_data/gsm8k/QA_record_prompt1.txt",
                 "../datasets/gpt35_zeroshotcot_training_data/aqua/QA_record_prompt1.txt"], help="dataset used for experiment"
    )

    parser.add_argument(
        "--model_id", type=str, default="gpt-35-turbo-0613", choices=["gpt-35-turbo-0613" ,"text-davinci-003", "tiiuae/falcon-7b-instruct"], help="model used for decoding."
    )

    parser.add_argument(
        "--max_token_len", type=float, default=86, help="maximum number of reasoning chains"
    )

    parser.add_argument(
        "--max_ra_len", type=float, default=12, help="maximum number of reasoning chains"
    )
    
    parser.add_argument("--random_seed", type=int, default=1, help="random seed")

    parser.add_argument(
        "--num_trails", type=int, default=5, help="number of trails to run for each qeestion"
    )

    parser.add_argument(
        "--method", type=str, default="cot", choices=["standard", "zero_shot_cot", "cot"], help="method"
    )
    parser.add_argument(
        "--dataset_size_limit", type=int, default=1000, help="limit the size of training data used to select the demonstrations"
    )
    parser.add_argument(
        "--sort_by", type=str, default='entropy', choices=['disagreement', 'variance', 'entropy'], help="sort the final result by given option"
    )
    parser.add_argument(
        "--temperature", type=float, default=0.7, help="temperature for llm decoding"
    )
    parser.add_argument(
        "--dir_prompts", type=str, default="uncertainty_estimation_prompts/gsm8k", help="prompts to use"
    )
    parser.add_argument(
        "--nr_demos", type=int, default=8, help='number of demonstrations'
    )

    parser.add_argument(
        "--answers_are_available", type=bool, default=True, help='true if answers are available in the test dataset, false otherwise'
    )

    # use the unsorted uncertainty file to select the demonstrations for Auto-Active-KMeans CoT
    parser.add_argument(
        "--load_uncertainty_file", type=str, default='final_uncertainties/2023_08_29_14_44_47/unsorted_all_uncertainty_records', help='nr of demonstrations to select'
    )

    parser.add_argument(
        "--load_uncertainty_args_file", type=str, default='final_uncertainties/2023_08_29_14_44_47/args.json', help='nr of demonstrations to select'
    )
    
    parser.add_argument(
        "--embedding_model_id", type=str, default="text-embedding-ada-002-v2", help="the id of the embedding model to use"
    )

    parser.add_argument(
        "--load_embeddings_file", type=str, default='embeddings/gsm8k/2023_08_29_22_56_01/embeddings.pkl', help='file to load embeddings from'
    )

    parser.add_argument(
        "--load_embeddings_args_file", type=str, default='embeddings/gsm8k/2023_08_29_22_56_01/args.json', help='file to load embeddings from; either None or a path to a file'
    )

    args = parser.parse_args()
    if args.dataset == "gsm8k":
        args.direct_answer_trigger = "\nTherefore, the answer (arabic numerals) is"

    elif args.dataset == "aqua":
        args.direct_answer_trigger = "\nThe answer is"
    else:
        raise NotImplementedError

    if args.answers_are_available:
        args.demos_save_dir = "labeled_demos"
    else:
        args.max_ra_len = 'None'
        args.demos_save_dir = "unlabeled_demos"
        
    # "Therefore, the answer ..." -> "The answer ..."
    trigger = args.direct_answer_trigger.replace("\nTherefore, ", "")
    args.direct_answer_trigger_for_zeroshot = trigger[0].upper() + trigger[1:]
    args.direct_answer_trigger_for_zeroshot_cot = args.direct_answer_trigger
    args.direct_answer_trigger_for_fewshot = "The answer is"
    args.cot_trigger = "Let's think step by step."

    return args


def main():
    args = parse_arguments()

    current_time = datetime.datetime.now()
    time_string = current_time.strftime("%Y_%m_%d_%H_%M_%S")
    if not os.path.exists(args.demos_save_dir):
        os.makedirs(args.demos_save_dir)
        os.makedirs(args.demos_save_dir + '/' + 'auto_active_kmeans')
        os.makedirs(args.demos_save_dir + '/' +  'auto_active_kmeans' + '/' + time_string)
        os.makedirs(args.demos_save_dir + '/' + 'auto_active_kmeans' + '/' + time_string + '/' + 'demos')
    elif not os.path.exists(args.demos_save_dir + '/' + 'auto_active_kmeans'):
        os.makedirs(args.demos_save_dir + '/' + 'auto_active_kmeans')
        os.makedirs(args.demos_save_dir + '/' +  'auto_active_kmeans' + '/' + time_string)
        os.makedirs(args.demos_save_dir + '/' + 'auto_active_kmeans' + '/' + time_string + '/' + 'demos')
    else:
        os.makedirs(args.demos_save_dir + '/' +  'auto_active_kmeans' + '/' + time_string)
        os.makedirs(args.demos_save_dir + '/' + 'auto_active_kmeans' + '/' + time_string + '/' + 'demos')

    args.args_file = args.demos_save_dir + '/' + 'auto_active_kmeans' + '/' + time_string + '/' + 'args.json'
    args.demos_save_dir = args.demos_save_dir + '/' + 'auto_active_kmeans' + '/' + time_string + '/'

    args_dict = {
        "sampling_method": "Auto-Active-KMeans",
        "dataset": args.dataset,
        "data_path": args.data_path,
        "dataset_size_limit": args.dataset_size_limit,
        "random_seed": args.random_seed,
        "nr_demos": args.nr_demos,
        "demos_save_dir": args.demos_save_dir,
        "answers_are_available": args.answers_are_available,
        "load_uncertainty_file": args.load_uncertainty_file,
        "load_uncertainty_args_file": args.load_uncertainty_args_file,
        "load_embeddings_file": args.load_embeddings_file,
        "load_embeddings_args_file": args.load_embeddings_args_file,
        }

    if 'zeroshotcot' in args.data_path:
        args_dict['max_ra_len'] = args.max_ra_len
        args_dict['max_token_len'] = args.max_token_len

    dataloader = create_dataloader(args)

    start = time.time()
    
    if args.load_embeddings_file and args.load_embeddings_args_file:
        with open(args.load_embeddings_file, 'rb') as read_f:
            corpus_embeddings = pickle.load(read_f)

        with open(args.load_embeddings_args_file, 'r', encoding="utf-8") as f:
            embeddings_args = json.load(f)

        args_dict['generate_embeddings_args'] = embeddings_args
    else:
        args_dict['embedding_model_id'] = args.embedding_model_id
        corpus_embeddings = generate_corpus_embeddings(args, dataloader)

    if args.load_uncertainty_file and args.load_uncertainty_args_file: 
        with open(args.load_uncertainty_file, 'r', encoding="utf-8") as f:
            all_uncertainty_records = json.load(f)['result']

        with open(args.load_uncertainty_args_file, 'r', encoding="utf-8") as f:
            uncertainty_args = json.load(f)

        uncertainty_metric = uncertainty_args['sort_by']
        args_dict['generate_uncertainty_args'] = uncertainty_args
    else:
        args_dict["method"] = args.method
        args_dict["model_id"] = args.model_id
        args_dict["num_trails"] = args.num_trails
        args_dict["sort_by"] = args.sort_by
        args_dict["temperature"] = args.temperature
        args_dict["dir_prompts"] = args.dir_prompts

        prompts_list = create_prompts_inference(args)
        assert len(prompts_list) == 1

        azure_llm = initialize_llm(args, is_azureopenai=True)
        azure_llm_chain = initialize_llmchain(azure_llm, prompts_list[0])

        openai_llm = initialize_llm(args, is_azureopenai=False)
        openai_llm_chain = initialize_llmchain(openai_llm, prompts_list[0])

    if 'zeroshotcot' in args.data_path:
        zeroshot_uncertainty_ranked_data = []
        for item in all_uncertainty_records:
            selected_example = dataloader[item['question_idx']]
            selected_example[uncertainty_metric] = item[uncertainty_metric]
            zeroshot_uncertainty_ranked_data.append(selected_example)

        #all_uncertainty_records = filter_examples_with_labels(args, zeroshot_uncertainty_ranked_data, args.max_token_len, args.max_ra_len)
        all_uncertainty_records = zeroshot_uncertainty_ranked_data
        args_dict['max_token_len'] = args.max_token_len
        args_dict['max_ra_len'] = args.max_ra_len

    clustering_model = KMeans(n_clusters=args.nr_demos, random_state=args.random_seed)
    clustering_model.fit(corpus_embeddings)
    cluster_assignments = clustering_model.labels_

    cluster_to_examples = [[] for i in range(args.nr_demos)]
    for example, cluster_id in zip(dataloader, cluster_assignments):
        cluster_to_examples[cluster_id].append(example)
    
    cluster_uncertainty_records_dic = {}
    cluster_nr_examples = []
    demos = []
    for cluster_id in range(args.nr_demos):
        print('\n' + '*' * 50 + '\n')
        print(f'Cluster {cluster_id} has {len(cluster_to_examples[cluster_id])} examples.\n')
        #cluster_examples_filtered = []
        cluster_examples = cluster_to_examples[cluster_id]    

        if args.answers_are_available:
            cluster_examples_filtered = filter_examples_with_labels(args, cluster_examples, args.max_token_len, args.max_ra_len)
        else:
            cluster_examples_filtered = filter_examples_no_labels(cluster_examples, 60)
        
        filtered_cluster_question_idxs = [example['question_idx'] for example in cluster_examples_filtered]
        print(f'After filtering out, Cluster {cluster_id} has {len(cluster_examples_filtered)} examples. These are examples idxs: {filtered_cluster_question_idxs}\n')

        if len(cluster_examples_filtered) > 0:
            if args.load_uncertainty_file:
                filtered_cluster_unsorted_uncertainty_records = [all_uncertainty_records[x] for x in filtered_cluster_question_idxs]
                filtered_cluster_sorted_uncertainty_records = sort_uncertainty(args, filtered_cluster_unsorted_uncertainty_records)
            else:
                filtered_cluster_sorted_uncertainty_records = generate_uncertainty_all_questions(args, cluster_examples_filtered, True, azure_llm_chain, openai_llm_chain)

            demos.append(filtered_cluster_sorted_uncertainty_records[0])
            cluster_uncertainty_records_dic[f'cluster_{cluster_id}'] = filtered_cluster_sorted_uncertainty_records
            print(f'Highest uncertainty example:\n{filtered_cluster_sorted_uncertainty_records[0]} \n')

            cluster_nr_examples.append({
            'cluster_id': cluster_id,
            'nr_total_examples': len(cluster_examples),
            'nr_filtered_examples': len(cluster_examples_filtered),
            'selected_example': filtered_cluster_sorted_uncertainty_records[0]
            }
            )
        else:
            print(f'After filtering out no examples left for cluster {cluster_id}.\n')

    end = time.time()
    args_dict["execution_time"] = str(end - start) + " seconds"

    with open(args.args_file, 'w') as f:
        json.dump(args_dict, f, indent=4)

    demos = {"demo": demos}
    with open(args.demos_save_dir + 'demos/demos', 'w', encoding="utf-8") as write_f:
        json.dump(demos, write_f, indent=4, ensure_ascii=False)

    with open(args.demos_save_dir + 'uncertainties_per_cluster', 'w', encoding="utf-8") as write_f:
        json.dump(cluster_uncertainty_records_dic, write_f, indent=4, ensure_ascii=False)

    with open(args.demos_save_dir + 'cluster_nr_examples.txt', 'w') as f:
        f.write(json.dumps(cluster_nr_examples, indent=4))

    print('Auto-Active-KMeans CoT finished!')

if __name__ == "__main__":
    main()