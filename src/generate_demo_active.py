# This file used to generate uncertainty score for each question
from utils import *
import time
import argparse
import numpy as np
import json
from scipy.stats import entropy
from utils import initialize_llmchain
import load_env_vars
from constant_vars import *

def main():
    args = arg_parser()
    if args.dataset == "gsm8k":
        prefix = prefix_gsm8k
    elif args.dataset == "aqua":
        prefix = prefix_aqua
    else:
        raise NotImplementedError("dataset not implemented")

    model_name = args.model_id.replace('/', '-')  
    model_name = model_name.replace('.', '-')
    temperature = str(args.temperature).replace('.', '-')  

    if not os.path.exists(args.demos_save_dir):
        os.makedirs(args.demos_save_dir)
        os.makedirs(args.demos_save_dir + 'active')
        os.makedirs(args.demos_save_dir + 'active/' + args.dataset)
        os.makedirs(args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}')
    elif not os.path.exists(args.demos_save_dir + 'active'):
        os.makedirs(args.demos_save_dir + 'active')
        os.makedirs(args.demos_save_dir + 'active/' + args.dataset)
        os.makedirs(args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}')
    elif not os.path.exists(args.demos_save_dir + 'active/' + args.dataset):
        os.makedirs(args.demos_save_dir + 'active/' + args.dataset)
        os.makedirs(args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}')
    elif not os.path.exists(args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}'):
        os.makedirs(args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}')
    else:
        print('Directory Already Exists!')
        sys.exit(0)

    args.demos_save_dir = args.demos_save_dir + f'active/{args.dataset}/model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}/'

    if not os.path.exists(args.uncertainty_scores_dir):
        os.makedirs(args.uncertainty_scores_dir)

    uncertainty_filepath = f"{args.uncertainty_scores_dir}method_active_{args.dataset}_model_{model_name}_method_{args.method}_numtrails_{args.num_trails}_sortby_{args.sort_by}_temperature_{temperature}_seed_{args.random_seed}_nrdemos_{args.nr_demos}_datasetsizelimit_{args.dataset_size_limit}.txt"
    
    print('Hyperparameters: ')

    set_random_seed(args.random_seed)
    dataloader = create_dataloader(args)

    if args.dataset_size_limit <= 0:
        args.dataset_size_limit = len(dataloader)
    else:
        dataloader = dataloader[:args.dataset_size_limit] # replace 7 with 1000; only take 1000 questions randomly to annotate, randomness decided by seed
    print(f"Proceeding with data size: {len(dataloader)}")
    print(f"model_id: {args.model_id}")
    print(f"method: {args.method}")
    print(f"num_trails: {args.num_trails}")
    print(f"sort_by: {args.sort_by}")
    print(f"temperature: {args.temperature}")
    print(f"random_seed: {args.random_seed}")
    print(f"nr_demos: {args.nr_demos}")
    
    if args.method == "few_shot_cot":
        args.prefix = prefix + ' Follow the format of the examples below:\n'
        given_prompt_list = create_several_input_prompts(args, cot_flag=True)
        assert len(given_prompt_list) == 1
        args.prompt = given_prompt_list[0]
    elif args.method == "zero_shot_cot":
        args.prompt = prefix + "\nQ: " + "{question}" + "\nA: Let's think step by step."
    
    print(f'PROMPT:\n{args.prompt}\n')
    args.llm_chain = initialize_llmchain(args.prompt, args)

    start =time.time()  
    result = create_uncertainty(args, dataloader)
    end = time.time()
    print('Total Execution Time: ', end - start, " seconds")

    demos = {'demo': result[:args.nr_demos]}
    with open(f"{args.demos_save_dir}demos", 'w', encoding="utf-8") as write_f:
        json.dump(demos, write_f, indent=4, ensure_ascii=False)

        
    with open(uncertainty_filepath, 'w') as f:
        try:
            f.write(json.dumps(result, indent=4))
        except:
            for item in result:
                try:
                    if args.dataset in ("gsm8k"):
                        f.write(f"{item}, uncertainty: {len(item[-1])}, variance: {item[1]}\n")
                    else:
                        f.write(f"{item}, uncertainty: {len(item[-1])}\n")
                except:
                    pass


def generate_uncertainty_qes(args, example):

    if args.dataset == "gsm8k":
        # the float is reserved for variance calculation result
        if args.answers_are_available:
            uncertainty_record = {'question': example['question'],
                                 'rationale': example['rationale'], 'final_answer': example['final_answer'] , 
                                 'variance':float, 'entropy':float, 'occurrence':{}}
        else:
            uncertainty_record = {'question': example['question'],
                                  'variance':float, 'entropy':float, 'occurrence':{}}
    else:
        if args.answers_are_available:
            uncertainty_record = {'question': example['question'],
                                'rationale': example['rationale'], 'final_answer': example['final_answer'],
                                'entropy':float, 'occurrence':{}}
        else:
            uncertainty_record = {'question': example['question'],
                                  'entropy':float, 'occurrence':{}}

    for _ in range(args.num_trails):
        response = args.llm_chain.run(question=example['question'])

        pred_ans = answer_extraction(args, response)
        #print(f'Single Trial Rationale:\n{response}')
        print(f'Single Trial Final Answer: {pred_ans}\n')

        # check uncertainty
        if pred_ans != "":
            if pred_ans in uncertainty_record['occurrence']:
                uncertainty_record['occurrence'][pred_ans] += 1 # increment answer occurrence
            else:
                uncertainty_record['occurrence'][pred_ans] = 1 # first occurence
        else:
            # Handle no solution case
            if NO_SOLUTION in uncertainty_record['occurrence']:
                uncertainty_record['occurrence'][NO_SOLUTION] += 1
            else:
                uncertainty_record['occurrence'][NO_SOLUTION] = 1

    # calculate the variance for the question (only applied to datasets with numerical answer)
    if args.dataset == "gsm8k":
        ans_list = []
        for ans, occurs in uncertainty_record['occurrence'].items():
            for i in range(int(occurs)):
                ans_list.append(float(ans))
        uncertainty_record['variance'] = np.var(ans_list)
        
    # calculate the entropy for all dataset
    frequency_list = list(uncertainty_record['occurrence'].values())
    uncertainty_record['entropy'] = entropy(frequency_list)

    # calculate the disagreement for all dataset
    uncertainty_record['disagreement'] = len(uncertainty_record['occurrence'])
    
    return uncertainty_record


# return a sorted list by uncertainty from high to low
def create_uncertainty(args, dataloader):
    result = []

    for example in dataloader:
        print(f'Question: {example["question"]}\n')
        uncertainty_record = generate_uncertainty_qes(args, example)
        print(f'Uncertainty Record: {uncertainty_record}')
        result.append(uncertainty_record)
        print('\n' + '*' * 60 + '\n')

    if args.sort_by == "disagreement":
        result.sort(key=lambda x: -len(x['occurrence']))
    elif args.sort_by == "variance" and args.dataset == "gsm8k":
        result.sort(key=lambda x: -x['variance'])
    elif args.sort_by == "entropy" :
        result.sort(key=lambda x:-x['entropy'])
    return result

def arg_parser():
    parser = argparse.ArgumentParser(description="Active_CoT")
    parser.add_argument("--random_seed", type=int, default=42, help="random seed")
    parser.add_argument(
        "--dataset", type=str, default="gsm8k", choices=["gsm8k", "aqua"], help="dataset to inference"
    )

    parser.add_argument(
        "--data_path", type=str, default="../datasets/gsm8k/train.jsonl",
        choices=["../datasets/gsm8k/train.jsonl", "../datasets/AQuA/train.json"], help="dataset used for experiment"
    )

    parser.add_argument(
        "--dir_prompts", type=str, default="uncertainty_estimation_prompts/gsm8k", help="prompts to use"
    )

    parser.add_argument(
        "--model_id", type=str, default="text-davinci-003", choices=["text-davinci-003", "tiiuae/falcon-7b-instruct"], help="model used for decoding."
    )
    
    parser.add_argument(
        "--method", type=str, default="few_shot_cot", choices=["zero_shot_cot", "few_shot_cot"], help="method"
    )
    
    parser.add_argument(
        "--dataset_size_limit", type=int, default=5, help="whether to limit dataset size. if 0, the dataset size is unlimited and we use all the samples in the dataset for creating the demonstrations."
    )
    
    parser.add_argument(
        "--temperature", type=float, default=0.7, help="temperature for llm decoding"
    )
    parser.add_argument(
        "--num_trails", type=int, default=3, help="number of trails to run for each qeestion"
    )
    parser.add_argument(
        "--sort_by", type=str, default='entropy', choices=['disagreement', 'variance', 'entropy'], help="sort the final result by given option"
    )

    parser.add_argument(
        "--nr_demos", type=int, default=3, help='nr of demonstrations to select'
    )

    parser.add_argument(
        "--answers_are_available", type=bool, default=True, help='true if answers are available in the test dataset, false otherwise'
    )

    parser.add_argument(
        "--uncertainty_scores_dir", type=str, default='uncertainty_scores/', help='directory where the uncertainty scores are saved'
    )
    
    args = parser.parse_args()
    
    # Fill in the dataset path
    if args.dataset == "gsm8k":
        args.direct_answer_trigger = "\nTherefore, the answer (arabic numerals) is"
    elif args.dataset == "aqua":
        args.direct_answer_trigger = "\nThe answer is"
    else:
        raise ValueError("dataset is not properly defined ...")

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


if __name__ == "__main__":
    main()