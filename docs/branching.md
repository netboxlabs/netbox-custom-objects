# Using NetBox Custom Objects with NetBox Branching

As of version 0.4.0 Custom Objects is _compatible_ with NetBox Branching, but not fully supported. This means that users can safely use both plugins together, but there are some caveats to be aware of. See below to learn how each of the Custom Objects models interacts with NetBox Branching.

> [!NOTE]
> We are working towards fully support for Custom Objects on branches. Keep an eye on the GitHub issues for updates ahead of future releases.  

> [!TIP]
> If you have any questions the best place to start is on the GitHub [discussions](https://github.com/netboxlabs/netbox-custom-objects/discussions). If you are a NetBox Labs customer, you can also contact support.  

## Custom Object Types and Custom Object Type Fields

Custom Object Types and Custom Object Type fields can be created, updated and deleted on branches, however the changes made on branches will be applied in main. This allows Custom Objects and Branching to be used safely alongside each other, but users should be aware of what this means.

- When you are in an activated branch any creates, updates and deletes you perform on Custom Object Types and Custom Object Type Fields will not show up in the Diff or Changes Ahead views
- Although you're in an activated branch, these changes will be made directly to main
- Typically it will be NetBox admins who are altering Custom Object Types and Custom Object Type Fields - we recommend that you experiment in your staging instance until you are satisfied with the modelling and then move them into prod

## Custom Objects

Changes to Custom Objects on branches are disallowed.

- When in an activated branch, users will still be able to see the available Custom Object Types and any Custom Objects that were brought into the branch upon branch creation, but will not be able to interact with them to affect changes on the branch.  
- This approach was chosen to make sure that users can safely use both Custom Objects and Branching together, while we are working on fuller support.  