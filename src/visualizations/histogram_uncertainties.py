import json 
import seaborn as sns 
import matplotlib.pyplot as plt

def plot_uncertainty_scores(uncertainty_filepath):
    with open(uncertainty_filepath, 'r', encoding="utf-8") as read_f:
        all_uncertainty_records = json.load(read_f)['result']

    entropy_scores = [record['entropy'] for record in all_uncertainty_records]
    sns.histplot(entropy_scores, bins=10)
    plt.savefig('entropy_scores.png')
    plt.show()

if __name__ == "__main__":
    # gsm8k
    #plot_uncertainty_scores('../final_uncertainties/2023_08_29_14_44_47/unsorted_all_uncertainty_records')

    # aqua
    plot_uncertainty_scores('../final_uncertainties/2023_08_30_00_02_11/unsorted_all_uncertainty_records')
